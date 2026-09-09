"""A stale Herdr ID never becomes native continuity or review approval."""

import copy
import io
import json
import os
from unittest.mock import patch
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teamlead import cli, recovery, report_delivery as delivery, state, supervision
from teamlead.assign import assignment_text
from tests import test_assign as dispatch_fixture
from tests import test_report_delivery as native_fixture
from tests import test_supervision_cli as supervised_fixture
from teamlead.errors import UsageError
from tests.test_report_delivery import AT, PANE, SESSION, encode, grok_row, grok_rows, identity
from teamlead.herdr import HerdrClient
from tests.fakes import FakeRunner, ScriptedReads, agent_json


class StaleGrokDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.case = native_fixture.NativeDeliveryTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.document, self.data = self.case.recovery_fixture()
        self.assignment = self.document['assignments'][0]
        self.dispatch = self.document['recovery']['dispatches'][0]
        self.observed = {**identity('grok'), 'value': 'old-before-new'}
        for row in (self.assignment, self.dispatch['result']):
            row.update(context_session=None, cleared=True, clear_reason='automatic')
        self.dispatch['observed_before'] = {'pane_id': PANE, 'context_session': {'pane_id': PANE, **self.observed}}
        self.dispatch['context_before_send'] = {'cleared': True, 'clear_reason': 'automatic', 'context_session': None}
        self.prompt = assignment_text('judge', self.dispatch['common'], self.dispatch['brief'])
        self.rows = [grok_row({'sessionUpdate': 'hook_execution', 'event_name': 'session_start'}),
                     grok_row({'sessionUpdate': 'hook_execution', 'event_name': 'user_prompt_submit', 'prompt_id': 'prompt-1'})]
        self.rows += grok_rows(self.case.marker)
        self.rows[2]['params']['update']['content']['text'] = self.prompt
        Path(self.data['pane']).write_text(json.dumps({'result': {'pane': {**self.case.pane, 'agent_session': self.observed}}}))
        Path(self.data['visible']).write_text('     ' + self.case.marker)
        Path(self.data['source']).write_text(encode(self.rows))
        plan = self.case.tmp / 'plan.json'
        plan.write_text(json.dumps({'assignments': {'judge': 'worker'}}))
        self.data['plan'] = str(plan)
        _, self.dispatch['fingerprint'] = recovery.dispatch_identity(
            self.dispatch['task'], 'judge', 'worker', None,
            {'common': self.dispatch['common'], 'judge': self.dispatch['brief']},
            options={'task': self.dispatch['task'], 'fix_round': None, 'plan': None,
                     'work': None, 'rounds': {}, 'retain_context': False, 'no_clear': False})

    def recover(self):
        return delivery.recover(self.document['recovery'], self.document['assignments'], self.data, AT)

    def test_known_stale_clear_keeps_observation_and_proves_source_separately(self):
        old = copy.deepcopy(self.document)
        record = self.recover()
        self.assertEqual(record['schema_version'], 2)
        self.assertEqual(record['native_session'], self.observed)
        self.assertEqual(record['source_session'], {'agent': 'grok', 'kind': 'id', 'value': SESSION})
        self.assertEqual(record['basis'], 'archived_grok_clear_source')
        self.assertIsNone(record['native_session_proof'])
        self.assertFalse(record['grants_review_approval'])
        self.assertEqual(self.document['assignments'], old['assignments'])
        for key in old['recovery']:
            if key not in ('delivery_recoveries', 'events'):
                self.assertEqual(self.document['recovery'][key], old['recovery'][key])
        recovery.validate_store(self.document['recovery'], self.document['assignments'])
        self.assertEqual(self.recover(), record)

    def test_public_owner_recovery_replays_without_worker_input_or_byte_rewrites(self):
        ledger_path, record_path = self.case.tmp / 'state.json', self.case.tmp / 'record.json'
        # Exercise the owner migration as well as writing a schema-2 receipt.
        self.document['recovery']['schema_version'] = 3
        state.save_state(ledger_path, self.document)
        record_path.write_text(json.dumps(self.data))
        original_bytes = {key: Path(value).read_bytes() for key, value in self.data.items() if key not in ('id', 'dispatch')}
        args = ['recover-report', '--state', str(ledger_path), '--record', str(record_path), '--now', AT]
        runner = FakeRunner()
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(cli.main(args, stdout=out, stderr=err, client=HerdrClient(runner=runner)), 0, err.getvalue())
        saved = ledger_path.read_bytes()
        self.assertEqual(json.loads(saved)['recovery']['schema_version'], 4)
        self.assertEqual(cli.main(args, stdout=io.StringIO(), stderr=io.StringIO()), 0)
        self.assertEqual(ledger_path.read_bytes(), saved)
        self.assertEqual(runner.calls, [])
        for key, before in original_bytes.items():
            self.assertEqual(Path(self.data[key]).read_bytes(), before)
        Path(self.data['report']).write_text('changed report bytes')
        self.assertEqual(cli.main(args, stdout=io.StringIO(), stderr=io.StringIO()), 1)
        self.assertEqual(ledger_path.read_bytes(), saved)

    def test_public_owner_validates_delivery_indices_before_dereferencing(self):
        self.recover()
        legacy = native_fixture.NativeDeliveryTests()
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        old_document, old_data = legacy.recovery_fixture()
        delivery.recover(old_document['recovery'], old_document['assignments'], old_data, AT)
        ledger, request = self.case.tmp / 'index-state.json', self.case.tmp / 'index-request.json'
        for document, data in ((old_document, old_data), (self.document, self.data)):
            state.add_assignment(document, AT, 'tester', 'another-worker')
            schema = document['recovery']['delivery_recoveries'][0]['schema_version']
            request.write_text(json.dumps(data))
            for index in (0, len(document['assignments']), -1, None, '0', 0.0, False, True, 1):
                candidate = copy.deepcopy(document)
                candidate['recovery']['delivery_recoveries'][0]['assignment_index'] = index
                state.save_state(ledger, candidate)
                before = ledger.read_bytes()
                valid = type(index) is int and index == 0
                for command in ('state', 'recover-report'):
                    args = ['bash', str(ROOT / 'teamlead.sh'), command, '--state', str(ledger)]
                    if command == 'recover-report':
                        args += ['--record', str(request), '--now', AT]
                    result = subprocess.run(args, capture_output=True, text=True, check=False)
                    with self.subTest(schema=schema, index=index, index_type=type(index), command=command):
                        self.assertNotIn('Traceback', result.stderr)
                        self.assertEqual(ledger.read_bytes(), before)
                        self.assertEqual(result.returncode, 0 if valid or command == 'state' else 1, result.stderr)
                        if valid:
                            self.assertEqual(result.stderr, '')
                            expected = candidate if command == 'state' else candidate['recovery']['delivery_recoveries'][0]
                            self.assertEqual(json.loads(result.stdout), expected)
                        else:
                            self.assertIn('restore the owner-written ledger', result.stderr)
                            self.assertIn('file is left untouched', result.stderr)
                            if command == 'state':
                                self.assertEqual(json.loads(result.stdout), state.empty_state())
                            else:
                                self.assertEqual(result.stdout, '')
                                self.assertIn('"error": "state_error"', result.stderr)

    def test_public_owner_scrollbar_only_requires_complete_bare_source_and_row(self):
        marker = self.case.marker
        row = '     ' + marker + '             █'
        cases = [(marker, '     ' + marker + suffix, True)
                 for suffix in ('', '    1:55 AM', '    1:55 AM   █', '             █')]
        cases += [(marker, visible, False) for visible in (
            row + '█', row + ' extra', '     ' + marker + ' █', '     ' + marker + '█',
            '     ' + marker + '\t\t█', '> ' + row, '- ' + row, '• ' + marker + '   █',
            row[1:], ' ' + row, '```' + row + '```', '     REPORT: \n' + str(self.case.report) + '   █')]
        cases += [(final, row, False) for final in (
            '- ' + marker, '> ' + marker, '```\n' + marker, '    ' + marker,
            '     ' + marker, marker + '   █', marker + '\nmore output')]
        ledger, request = self.case.tmp / 'scrollbar-state.json', self.case.tmp / 'scrollbar-request.json'
        request.write_text(json.dumps(self.data))
        for final, visible, expected in cases:
            rows = copy.deepcopy(self.rows)
            rows[3]['params']['update']['content']['text'] = final
            Path(self.data['source']).write_text(encode(rows))
            Path(self.data['visible']).write_text(visible)
            state.save_state(ledger, self.document)
            before = ledger.read_bytes()
            result = subprocess.run(['bash', str(ROOT / 'teamlead.sh'), 'recover-report',
                '--state', str(ledger), '--record', str(request), '--now', AT],
                capture_output=True, text=True, check=False)
            with self.subTest(final=final, visible=visible):
                self.assertEqual(result.returncode, 0 if expected else 1, result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                if expected:
                    receipt = json.loads(result.stdout)
                    self.assertTrue(receipt['found'])
                    self.assertEqual(receipt['native_session'], self.observed)
                    self.assertEqual(receipt['source_session']['value'], SESSION)
                    self.assertIsNone(receipt['native_session_proof'])
                    self.assertFalse(receipt['grants_review_approval'])
                    saved = json.loads(ledger.read_text())
                    self.assertEqual(saved['assignments'], self.document['assignments'])
                    self.assertEqual(saved['recovery']['dispatches'], self.document['recovery']['dispatches'])
                else:
                    self.assertEqual(result.stdout, '')
                    self.assertIn('bare final marker', result.stderr)
                    self.assertEqual(ledger.read_bytes(), before)

    def test_dispatch_brief_common_and_plan_must_match_original_fingerprint(self):
        for key in ('brief', 'common', 'plan'):
            path = Path(self.data[key] if key == 'plan' else self.dispatch[key])
            body = path.read_text()
            changed = json.dumps({'assignments': {'judge': 'worker'}, 'rounds': {'judge': {'type': 'build'}}}) if key == 'plan' else body + '\nChanged scope\n'
            path.write_text(changed)
            before = copy.deepcopy(self.document)
            with self.subTest(key=key), self.assertRaisesRegex(UsageError, 'grok_dispatch_unbound'):
                self.recover()
            self.assertEqual(self.document, before)
            path.write_text(body)

    def test_reused_prompt_paths_and_changed_plan_task_refuse(self):
        other = copy.deepcopy(self.dispatch)
        other['id'] = 'other-dispatch'
        self.document['recovery']['dispatches'].append(other)
        before = copy.deepcopy(self.document)
        with self.assertRaisesRegex(UsageError, 'grok_dispatch_ambiguous'):
            self.recover()
        self.assertEqual(self.document, before)
        self.document['recovery']['dispatches'].pop()
        plan = Path(self.data['plan'])
        plan.write_text(json.dumps({'assignments': {'judge': 'worker'}, 'task_context': {'task': 'another-task'}}))
        with self.assertRaisesRegex(UsageError, 'grok_dispatch_unbound'):
            self.recover()

    def test_source_ambiguities_and_failed_or_incomplete_turns_refuse(self):
        candidates = [self.rows[1:], self.rows[:-1], self.rows + self.rows,
                      self.rows[:3] + self.rows[2:],
                      self.rows[:-1] + [grok_row({'sessionUpdate': 'turn_failed'})] + self.rows[-1:],
                      self.rows[:-1] + [grok_row({'sessionUpdate': 'turn_cancelled'})] + self.rows[-1:]]
        for field, value in [('sessionId', 'other-native'), ('sessionId', []), ('_meta', [])]:
            rows = copy.deepcopy(self.rows)
            rows[-1]['params'][field] = value
            candidates.append(rows)
        for update in ({'prompt_id': 'other-prompt'}, {'stop_reason': 'refusal'}):
            rows = copy.deepcopy(self.rows)
            rows[-1]['params']['update'].update(update)
            candidates.append(rows)
        rows = copy.deepcopy(self.rows)
        rows[2]['params']['update']['content']['text'] = 'another original dispatch'
        candidates.append(rows)
        rows = copy.deepcopy(self.rows)
        rows[3]['params']['_meta']['promptId'] = 'another-prompt'
        candidates.append(rows)
        for rows in candidates:
            Path(self.data['source']).write_text(encode(rows))
            before = copy.deepcopy(self.document)
            with self.subTest(rows=rows), self.assertRaisesRegex(UsageError, 'grok_source_ambiguous'):
                self.recover()
            self.assertEqual(self.document, before)

    def test_authored_or_wrapped_markers_do_not_gain_stale_identity_exception(self):
        for final in ('- ' + self.case.marker, '     ' + self.case.marker, '```\n' + self.case.marker,
                      '> example\n' + self.case.marker, self.case.marker + '\nmore output'):
            rows = copy.deepcopy(self.rows)
            rows[3]['params']['update']['content']['text'] = final
            Path(self.data['source']).write_text(encode(rows))
            with self.subTest(final=final), self.assertRaisesRegex(UsageError, 'bare final marker'):
                self.recover()
        Path(self.data['source']).write_text(encode(self.rows))
        Path(self.data['visible']).write_text('     REPORT: \n' + str(self.case.report))
        with self.assertRaisesRegex(UsageError, 'bare final marker'):
            self.recover()

    def test_known_continuity_and_unproven_clears_cannot_be_replaced(self):
        for target, key, value in ((self.assignment, 'context_session', {'pane_id': PANE, **self.observed}),
                (self.dispatch['context_before_send'], 'cleared', False),
                (self.dispatch['result'], 'clear_reason', 'hand'),
                (self.dispatch['observed_before'], 'pane_id', 'another-pane'),
                (self.dispatch['observed_before'], 'context_session', None)):
            original = target[key]
            target[key] = value
            before = copy.deepcopy(self.document)
            with self.subTest(key=key), self.assertRaisesRegex(UsageError, 'grok_clear_identity_unproven'):
                self.recover()
            self.assertEqual(self.document, before)
            target[key] = original
        negative = json.loads(Path(self.data['wait_receipt']).read_text())
        negative['reason'] = 'terminal_provider_refusal'
        Path(self.data['wait_receipt']).write_text(json.dumps(negative))
        with self.assertRaisesRegex(UsageError, 'refusals are not delivery'):
            self.recover()

    def test_owner_reader_rejects_forged_separate_identity_and_approval(self):
        record = self.recover()
        for key, value in (('source_session', {'agent': 'grok', 'kind': 'id', 'value': self.observed['value']}),
                           ('native_session', identity('codex')), ('native_session_proof', identity('grok')),
                           ('grants_review_approval', True), ('basis', 'archived_native_final_source')):
            original = record[key]
            record[key] = value
            with self.subTest(key=key), self.assertRaises(UsageError):
                recovery.validate_store(self.document['recovery'], self.document['assignments'])
            record[key] = original

    def test_replay_rejects_a_changed_source_identity_even_with_same_receipts(self):
        record = self.recover()
        record['source_session']['value'] = 'unproven-different-native'
        with self.assertRaisesRegex(UsageError, 'different evidence'):
            self.recover()

    def test_migration_preserves_original_receipts_and_refuses_newer_embedded_records(self):
        legacy = native_fixture.NativeDeliveryTests()
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        document, data = legacy.recovery_fixture()
        original_receipt = delivery.recover(document['recovery'], document['assignments'], data, AT)
        document['recovery']['schema_version'] = 3
        before = copy.deepcopy(document)
        self.assertTrue(recovery.migrate_store(document['recovery']))
        self.assertEqual(document['recovery'], {**before['recovery'], 'schema_version': 4})
        self.assertEqual(document['recovery']['delivery_recoveries'], [original_receipt])
        record = self.recover()
        self.document['recovery']['schema_version'] = 3
        with self.assertRaisesRegex(UsageError, 'unowned newer delivery'):
            recovery.migrate_store(self.document['recovery'])
        self.assertEqual(record['schema_version'], 2)

    def test_native_clock_and_viewport_scrollbar_require_bare_source(self):
        row = '     ' + self.case.marker + '    1:55 AM   █'
        Path(self.data['visible']).write_text(row)
        self.assertTrue(self.recover()['found'])
        self.assertIsNone(delivery.decorated_row(row + '█', 'grok', str(self.case.report)))
        self.assertIsNone(delivery.decorated_row(row + ' extra', 'grok', str(self.case.report)))
        self.assertFalse(delivery.bare_final(self.case.marker + '   █', str(self.case.report)))

    def test_grok_source_storage_honors_isolated_native_home(self):
        home = self.case.tmp / 'private-native-home'
        with patch.dict(os.environ, {'GROK_HOME': str(home)}):
            self.assertEqual(delivery.source_root('grok'), home / 'sessions')

    def test_report_changed_during_verification_cannot_get_a_receipt(self):
        real_receipt = recovery.receipt
        def change_report(path):
            result = real_receipt(path)
            if path == self.data['plan']:
                Path(self.data['report']).write_text('changed during verification')
            return result
        before = copy.deepcopy(self.document)
        with patch.object(recovery, 'receipt', side_effect=change_report):
            with self.assertRaisesRegex(UsageError, 'grok_evidence_changed'):
                self.recover()
        self.assertEqual(self.document, before)

    def test_fresh_developer_clear_never_records_a_late_stale_herdr_id(self):
        case = dispatch_fixture.ApplyTest()
        case.setUp()
        self.addCleanup(case.tearDown)
        runner = dispatch_fixture.runner_with({'grok': 'idle'})
        stale = agent_json('grok', 'idle', 'w4:p1', session_id=self.observed['value'])
        runner.responses['agent get grok'] = ScriptedReads([agent_json('grok', 'idle', 'w4:p1'), stale, stale])
        warnings = []
        result = dispatch_fixture.apply(HerdrClient(runner=runner), {'developer': 'grok'},
            dispatch_fixture.BY_NAME, case.paths, AT, task='fresh-grok', warn=warnings.append)
        row = result['applied'][0]
        self.assertEqual(row['status'], 'applied')
        self.assertTrue(row['cleared'])
        self.assertIsNone(row['context_session'])
        self.assertTrue(any('stale-ID recovery is unavailable' in message
                            and 'record the report as unavailable and notify the operator' in message
                            and 'Keep review/release gates unsatisfied' in message for message in warnings))
        self.assertEqual(sum(call[1:3] == ['agent', 'prompt'] for call in runner.calls), 1)


class SupervisedStaleGrokDeliveryTests(StaleGrokDeliveryTests):
    """Every stale-Grok proof and refusal again under a bound round's fingerprint (#387).

    A bound round's apply stores each new dispatch fingerprint wrapped with the
    role's exact report path. Recovery must reproduce that binding from the
    report the record names, and nothing else may.
    """

    def setUp(self):
        super().setUp()
        self.legacy_fingerprint = self.dispatch['fingerprint']
        self.dispatch['fingerprint'] = supervision.report_bound_fingerprint(self.legacy_fingerprint, self.data['report'])

    def legacy_identity(self):
        return recovery.dispatch_identity(
            self.dispatch['task'], 'judge', 'worker', None,
            {'common': self.dispatch['common'], 'judge': self.dispatch['brief']},
            options={'task': self.dispatch['task'], 'fix_round': None, 'plan': None,
                     'work': None, 'rounds': {}, 'retain_context': False, 'no_clear': False})[1]

    def test_bound_fingerprint_differs_from_legacy_and_recovers_the_bound_report(self):
        self.assertNotEqual(self.dispatch['fingerprint'], self.legacy_fingerprint)
        record = self.recover()
        self.assertEqual(record['schema_version'], 2)
        self.assertEqual(record['basis'], 'archived_grok_clear_source')
        self.assertEqual(record['source_session'], {'agent': 'grok', 'kind': 'id', 'value': SESSION})
        self.assertEqual(record['native_session'], self.observed)
        self.assertIsNone(record['native_session_proof'])
        self.assertFalse(record['grants_review_approval'])
        recovery.validate_store(self.document['recovery'], self.document['assignments'])
        self.assertEqual(self.recover(), record)

    def test_only_the_dispatched_report_path_reproduces_the_binding(self):
        body, plan_body = Path(self.data['source']).read_text(), Path(self.data['plan']).read_text()
        source = delivery.stale_grok_source(self.dispatch, self.assignment, self.observed, body, self.prompt,
                                            plan_body, report=self.data['report'])
        self.assertEqual(source['value'], SESSION)
        for wrong in (str(self.case.tmp / 'other-report.md'), self.data['report'] + '/',
                      str(Path(self.data['report']).parent), ''):
            with self.subTest(report=wrong), self.assertRaisesRegex(UsageError, 'grok_dispatch_unbound'):
                delivery.stale_grok_source(self.dispatch, self.assignment, self.observed, body, self.prompt,
                                           plan_body, report=wrong)
        for forged in (self.legacy_fingerprint + '0',
                       supervision.report_bound_fingerprint(self.legacy_fingerprint, str(self.case.tmp / 'other-report.md')),
                       supervision.report_bound_fingerprint(self.dispatch['fingerprint'], self.data['report']),
                       supervision.digest(self.legacy_fingerprint),
                       supervision.digest({'dispatch': self.legacy_fingerprint, 'report': self.data['report'], 'role': 'judge'})):
            self.dispatch['fingerprint'] = forged
            before = copy.deepcopy(self.document)
            with self.subTest(fingerprint=forged), self.assertRaisesRegex(UsageError, 'grok_dispatch_unbound'):
                self.recover()
            self.assertEqual(self.document, before)

    def test_a_report_delivered_to_another_assigned_path_refuses_on_the_binding_alone(self):
        # The brief assigns two report paths; the dispatch was bound to the
        # first. Complete evidence for the second passes every other check and
        # still refuses, so the binding is the one distinguishing input.
        other = self.case.tmp / 'other-report.md'
        other.write_bytes(self.case.report.read_bytes())
        brief = Path(self.dispatch['brief'])
        brief.write_text(brief.read_text() + 'REPORT: ' + str(other) + '\n')
        self.dispatch['fingerprint'] = supervision.report_bound_fingerprint(self.legacy_identity(), self.data['report'])
        self.prompt = assignment_text('judge', self.dispatch['common'], self.dispatch['brief'])
        self.rows[2]['params']['update']['content']['text'] = self.prompt
        Path(self.data['source']).write_text(encode(self.rows))
        trial = copy.deepcopy(self.document)
        self.assertTrue(delivery.recover(trial['recovery'], trial['assignments'], self.data, AT)['found'])
        marker = 'REPORT: ' + str(other)
        rows = copy.deepcopy(self.rows)
        rows[3]['params']['update']['content']['text'] = marker
        Path(self.data['source']).write_text(encode(rows))
        Path(self.data['visible']).write_text('     ' + marker)
        negative = json.loads(Path(self.data['wait_receipt']).read_text())
        negative['report_path'] = str(other)
        Path(self.data['wait_receipt']).write_text(json.dumps(negative))
        self.data['report'] = str(other)
        before = copy.deepcopy(self.document)
        with self.assertRaisesRegex(UsageError, 'grok_dispatch_unbound'):
            self.recover()
        self.assertEqual(self.document, before)

    def test_bound_apply_dispatch_recovers_its_completed_stale_id_report(self):
        # A real bound round: apply through the CLI with its report map, a
        # Grok automatic clear whose Herdr ID stays stale, then owner recovery
        # of the completed turn from archived originals on the same ledger.
        case = supervised_fixture.SupervisionCliTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        report = case.reports['tester']
        marker = 'REPORT: ' + report
        case.briefs['tester'].write_text('# tester\nVerify the pushed tip.\n' + marker + '\n')
        stale = {**identity('grok'), 'value': 'stale-before-new'}
        client = case._client({'grok': 'idle'}, sessions={'grok': stale['value']})
        code, out, err = case.invoke(case.apply_arguments({'tester': 'grok'}), client)
        self.assertEqual(code, 0, err)
        applied = json.loads(out)['applied'][0]
        self.assertEqual((applied['cleared'], applied['clear_reason'], applied['context_session']), (True, 'automatic', None))
        original = json.loads(case.state.read_text())
        dispatch = next(row for row in original['recovery']['dispatches'] if row['id'] == applied['dispatch_id'])
        _, legacy = recovery.dispatch_identity(
            supervised_fixture.TASK, 'tester', 'grok', None, {'common': dispatch['common'], 'tester': dispatch['brief']},
            options={'task': supervised_fixture.TASK, 'fix_round': None, 'plan': None, 'work': None,
                     'rounds': {}, 'retain_context': False, 'no_clear': False})
        self.assertNotEqual(dispatch['fingerprint'], legacy)
        self.assertEqual(dispatch['fingerprint'], supervision.report_bound_fingerprint(legacy, report))
        prompt = next(call[4] for call in case.runner.calls if call[1:3] == ['agent', 'prompt'])
        rows = [grok_row({'sessionUpdate': 'hook_execution', 'event_name': 'session_start'}),
                grok_row({'sessionUpdate': 'hook_execution', 'event_name': 'user_prompt_submit', 'prompt_id': 'prompt-1'})]
        rows += grok_rows(marker)
        rows[2]['params']['update']['content']['text'] = prompt
        Path(report).write_text('Tester report bytes.\n')
        pane = {'pane_id': applied['pane_id'], 'agent_status': 'done', 'agent_session': stale,
                'terminal_id': 'terminal-1', 'revision': 1, 'scroll': {'offset_from_bottom': 0}}
        artifacts = {'source': encode(rows), 'pane': json.dumps({'result': {'pane': pane}}), 'visible': '     ' + marker,
                     'wait_receipt': json.dumps({'agent': 'grok', 'report_path': report, 'state': 'done', 'found': False,
                                                 'reason': 'report file present, worker done on 2 consecutive reads, marker unconfirmed'}),
                     'plan': json.dumps({'tester': 'grok'})}
        data = {'id': 'bound-apply-recovery', 'dispatch': applied['dispatch_id'], 'report': report}
        for key, value in artifacts.items():
            path = case.tmp / ('bound-' + key + '.txt')
            path.write_text(value)
            data[key] = str(path)
        record = case.tmp / 'recover.json'
        record.write_text(json.dumps(data))
        calls_before = list(case.runner.calls)
        arguments = ['recover-report', '--record', str(record), '--now', supervised_fixture.AT]
        code, out, err = case.invoke(arguments, client)
        self.assertEqual(code, 0, err)
        receipt = json.loads(out)
        self.assertEqual((receipt['schema_version'], receipt['basis']), (2, 'archived_grok_clear_source'))
        self.assertEqual(receipt['native_session'], stale)
        self.assertEqual(receipt['source_session'], {'agent': 'grok', 'kind': 'id', 'value': SESSION})
        self.assertIsNone(receipt['native_session_proof'])
        self.assertFalse(receipt['grants_review_approval'])
        recovered = json.loads(case.state.read_text())
        self.assertEqual(recovered['assignments'], original['assignments'])
        self.assertEqual(recovered['recovery']['dispatches'], original['recovery']['dispatches'])
        self.assertEqual(recovered['recovery']['delivery_recoveries'], [receipt])
        self.assertEqual(case.runner.calls, calls_before)
        saved = case.state.read_bytes()
        code, out, err = case.invoke(arguments, client)
        self.assertEqual((code, json.loads(out)), (0, receipt), err)
        self.assertEqual(case.state.read_bytes(), saved)
        other = case.tmp / 'other-report.md'
        other.write_bytes(Path(report).read_bytes())
        record.write_text(json.dumps({**data, 'id': 'bound-apply-other-report', 'report': str(other)}))
        code, out, err = case.invoke(arguments, client)
        self.assertEqual((code, out), (1, ''))
        self.assertIn('does not assign this report path', err)
        self.assertEqual(case.state.read_bytes(), saved)
        self.assertEqual(case.runner.calls, calls_before)


if __name__ == '__main__':
    unittest.main()
