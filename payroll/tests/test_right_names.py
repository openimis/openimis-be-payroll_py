"""
Payroll's state transitions must check rights that name them.

`close`, `reject` and `make payment` are not a delete and not a create, but each was
checking whichever of those it happened to be wired to - so a role granted "delete
payroll" could close and reject, and a role granted "create payroll" could disburse
money. They now have their own names.

The ids are deliberately **aliased** onto the ones already in use, so nothing a role
can do changes yet. The aliasing is asserted here on purpose: when the rights are
split for real (202003 is the free slot, and a disbursement arguably deserves its own)
these assertions must be updated together with the migration that grants them.
"""

import ast
import inspect
import re

from django.test import TestCase

from payroll import gql_mutations
from payroll.apps import PayrollConfig

EXPECTED_PERMS = {
    "ClosePayrollMutation": {"gql_payroll_close_perms"},
    "RejectPayrollMutation": {"gql_payroll_reject_perms"},
    "MakePaymentForPayrollMutation": {"gql_payroll_make_payment_perms"},
}

PERM_REF = re.compile(r"PayrollConfig\.(\w*perms\w*)")


def _perms_referenced(class_name):
    source = inspect.getsource(getattr(gql_mutations, class_name))
    return set(PERM_REF.findall(ast.unparse(ast.parse(source))))


class PayrollRightNameTestCase(TestCase):
    def test_each_transition_checks_its_own_named_right(self):
        for class_name, expected in EXPECTED_PERMS.items():
            with self.subTest(mutation=class_name):
                self.assertEqual(_perms_referenced(class_name), expected)

    def test_transitions_no_longer_check_delete_or_create(self):
        for class_name in EXPECTED_PERMS:
            with self.subTest(mutation=class_name):
                referenced = _perms_referenced(class_name)
                self.assertNotIn("gql_payroll_delete_perms", referenced)
                self.assertNotIn("gql_payroll_create_perms", referenced)

    def test_the_new_rights_are_configured(self):
        for perm_name in (
            "gql_payroll_close_perms",
            "gql_payroll_reject_perms",
            "gql_payroll_make_payment_perms",
        ):
            with self.subTest(perm_name=perm_name):
                self.assertTrue(
                    getattr(PayrollConfig, perm_name),
                    f"{perm_name} is empty - `has_perms` would grant it to everyone",
                )

    def test_ids_are_still_aliased_onto_the_old_rights(self):
        """
        Intentional for now: renaming must not change who can do what. Splitting these
        onto their own ids is a separate change, with a migration.
        """
        self.assertEqual(
            PayrollConfig.gql_payroll_close_perms,
            PayrollConfig.gql_payroll_delete_perms,
        )
        self.assertEqual(
            PayrollConfig.gql_payroll_reject_perms,
            PayrollConfig.gql_payroll_delete_perms,
        )
        self.assertEqual(
            PayrollConfig.gql_payroll_make_payment_perms,
            PayrollConfig.gql_payroll_create_perms,
        )
