"""Specialist plans retain their original report-recovery fingerprint."""

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teamlead import recovery, report_delivery, supervision
from teamlead.assign import assignment_text
from teamlead.errors import UsageError
from tests import test_stale_grok_delivery as fixture
from tests.test_report_delivery import AT, SESSION, encode


class SpecialistDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.StaleGrokDeliveryTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def prepare(self, role, requirements):
        case = self.case
        for row in (case.assignment, case.dispatch, case.dispatch['result']):
            row['role'] = role
            if requirements is not None:
                row['requirements'] = requirements
            if role == 'reviewer':
                row['reviewer_scope'] = 'verification'
        case.dispatch['schema_version'] = case.dispatch['result']['schema_version'] = 2
        case.prompt = assignment_text(role, case.dispatch['common'], case.dispatch['brief'])
        case.rows[2]['params']['update']['content']['text'] = case.prompt
        Path(case.data['source']).write_text(encode(case.rows))
        plan = {'schema_version': 5, 'assignments': {role: 'worker'}}
        options = {'task': case.dispatch['task'], 'fix_round': None, 'plan': None,
                   'work': None, 'rounds': {}, 'retain_context': False, 'no_clear': False}
        if requirements is not None:
            plan['requirements'] = {role: requirements}
            options['requirements'] = plan['requirements']
        Path(case.data['plan']).write_text(json.dumps(plan))
        _, fingerprint = recovery.dispatch_identity(
            case.dispatch['task'], role, 'worker', None,
            {'common': case.dispatch['common'], role: case.dispatch['brief']}, options=options)
        case.dispatch['fingerprint'] = supervision.report_bound_fingerprint(fingerprint, case.data['report'])
        return plan

    def test_bound_consultation_recovers_without_changing_contribution_or_continuity(self):
        requirement = {'specialty': 'ux-product', 'required_capabilities': ['ux'],
                       'independent': False, 'engagement': 'onboarding-ux'}
        self.prepare('advisor', requirement)
        before = copy.deepcopy(self.case.document)
        result = self.case.recover()
        self.assertEqual(result['source_session']['value'], SESSION)
        self.assertFalse(result['grants_review_approval'])
        self.assertIsNone(result['native_session_proof'])
        self.assertEqual(self.case.document['assignments'], before['assignments'])
        self.assertEqual(self.case.dispatch, before['recovery']['dispatches'][0])
        recovery.validate_store(self.case.document['recovery'], self.case.document['assignments'])
        self.assertEqual(self.case.recover(), result)

    def test_changed_requirement_or_missing_plan_requirement_cannot_recover(self):
        requirement = {'specialty': 'security', 'required_capabilities': ['security'],
                       'independent': True, 'engagement': 'token-review'}
        original = self.prepare('reviewer', requirement)
        for field, value in (('engagement', 'another-review'), ('independent', False),
                             ('required_capabilities', ['ux']), ('specialty', 'ux')):
            plan = copy.deepcopy(original)
            plan['requirements']['reviewer'][field] = value
            Path(self.case.data['plan']).write_text(json.dumps(plan))
            before = copy.deepcopy(self.case.document)
            with self.subTest(field=field), self.assertRaises(UsageError):
                self.case.recover()
            self.assertEqual(self.case.document, before)
        original.pop('requirements')
        Path(self.case.data['plan']).write_text(json.dumps(original))
        with self.assertRaisesRegex(UsageError, 'grok_dispatch_unbound'):
            self.case.recover()

    def test_scope_only_reviewer_keeps_legacy_fingerprint_inputs(self):
        self.prepare('reviewer', None)
        result = self.case.recover()
        self.assertEqual(result['at'], AT)
        self.assertEqual(self.case.dispatch['reviewer_scope'], 'verification')
        self.assertNotIn('requirements', self.case.dispatch)
        recovery.validate_store(self.case.document['recovery'], self.case.document['assignments'])


if __name__ == '__main__':
    unittest.main()
