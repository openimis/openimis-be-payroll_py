"""
Guard rails on payroll's rights declaration.

Same structure as `claim` and `core`: `DJANGO_PERMS` by entity then by action,
`_PERM_CFG` deriving the config keys from it, and `Model.get_rights` which is only an
access point.

What is particular to payroll is the identifier sharing: 202004 serves to delete, close
and reject a payroll all at once, and 202002 both to create it and to pay. That is a
deployed state of affairs, not a copy-paste - the hierarchical shape makes it visible
and this test locks it down by name, so that a split (each with its own integer, plus a
migration granting it to the roles holding the old one) has to come through here.

What is locked down:
  * the identifiers as deployed and as `permissions_map.json` carries them;
  * the coverage of every declared action by a config key - a key with no class
    attribute is never loaded by `__load_config` and reading it raises AttributeError;
  * that no rights list is empty: `has_perms([])` returns True, so an empty list grants
    to everybody.
"""

import json
import os

from django.test import TestCase

from payroll.apps import (
    DJANGO_PERMS,
    PayrollConfig,
    _PERM_CFG,
    configured_perms,
    django_perms,
    perms,
)
from payroll.models import (
    BenefitAttachment,
    BenefitConsumption,
    CsvReconciliationUpload,
    PaymentPoint,
    Payroll,
    PayrollBenefitConsumption,
    PayrollBill,
)

# The identifiers as deployed. Changing one is incompatible with the existing roles:
# this test has to be updated *and* the new right granted.
EXPECTED_RIGHTS = {
    "gql_payment_point_search_perms": ["201001"],
    "gql_payment_point_create_perms": ["201002"],
    "gql_payment_point_update_perms": ["201003"],
    "gql_payment_point_delete_perms": ["201004"],
    "gql_payroll_search_perms": ["202001"],
    "gql_payroll_create_perms": ["202002"],
    "gql_payroll_delete_perms": ["202004"],
    "gql_payroll_close_perms": ["202004"],
    "gql_payroll_reject_perms": ["202004"],
    "gql_payroll_make_payment_perms": ["202002"],
    "gql_payment_gateway_config_perms": ["202005"],
    "gql_csv_reconciliation_search_perms": ["206001"],
    "gql_csv_reconciliation_create_perms": ["206002"],
}

# The `permissions_map.json` keys that carry these same identifiers.
EXPECTED_MAP_ENTRIES = {
    "payroll.payment_point_search": "201001",
    "payroll.payment_point_create": "201002",
    "payroll.payment_point_update": "201003",
    "payroll.payment_point_delete": "201004",
    "payroll.payroll_search": "202001",
    "payroll.payroll_create": "202002",
    "payroll.payroll_delete": "202004",
    "payroll.payroll_close": "202004",
    "payroll.payroll_reject": "202004",
    "payroll.payroll_make_payment": "202002",
    "payroll.payment_gateway_config": "202005",
    "payroll.csv_reconciliation_search": "206001",
    "payroll.csv_reconciliation_create": "206002",
}

# Actions that deliberately share another action's right, and which one. Until the
# split has happened, close/reject lean on the delete integer and pay on the create
# one.
INTENTIONALLY_SHARED = {
    ("payroll", "close"): ("payroll", "delete"),
    ("payroll", "reject"): ("payroll", "delete"),
    ("payroll", "makePayment"): ("payroll", "create"),
}


def _load_permissions_map():
    """`permissions_map.json` lives in the assembly, not in the package."""
    from django.conf import settings

    candidates = [
        os.path.join(str(settings.BASE_DIR), "permissions_map.json"),
        os.path.join(os.path.dirname(str(settings.BASE_DIR)), "permissions_map.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path) as handle:
                return json.load(handle)
    return None


class PayrollPermissionDeclarationTestCase(TestCase):
    def test_right_ids_unchanged(self):
        self.assertEqual(
            {key: getattr(PayrollConfig, key) for key in EXPECTED_RIGHTS},
            EXPECTED_RIGHTS,
        )

    def test_perm_cfg_covers_every_declared_action(self):
        declared = {
            (entity, action)
            for entity, actions in DJANGO_PERMS.items()
            for action in actions
        }
        self.assertEqual(set(_PERM_CFG.values()), declared)

    def test_perm_cfg_matches_config_attributes(self):
        """`__load_config` ignores the keys with no class attribute."""
        missing = [key for key in _PERM_CFG if not hasattr(PayrollConfig, key)]
        self.assertEqual(missing, [])

    def test_no_right_list_is_empty(self):
        empty = [key for key in _PERM_CFG if not getattr(PayrollConfig, key)]
        self.assertEqual(empty, [])

    def test_attributes_carry_the_declared_right(self):
        """
        The rights are constants set from DJANGO_PERMS: the attribute must equal the
        declaration, without going through the config.
        """
        for key, (entity, action) in _PERM_CFG.items():
            with self.subTest(key=key):
                self.assertEqual(getattr(PayrollConfig, key), perms(entity, action))

    def test_shared_right_ids_are_only_the_intended_ones(self):
        seen = {}
        for entity, actions in DJANGO_PERMS.items():
            for action, (_, right_id) in actions.items():
                seen.setdefault(right_id, []).append((entity, action))
        for right_id, holders in seen.items():
            if len(holders) == 1:
                continue
            for holder in holders:
                with self.subTest(right=right_id, holder=holder):
                    target = INTENTIONALLY_SHARED.get(holder)
                    self.assertTrue(
                        target is None or target in holders,
                        f"{holder} shares right {right_id} without that being intended",
                    )

    def test_the_shared_ids_are_still_the_deployed_ones(self):
        """
        The sharing is intentional *today*: renaming must not change who can do what.
        The day each takes its own integer, these equalities fall - together with the
        migration granting the new right to the roles holding the old one.
        """
        self.assertEqual(perms("payroll", "close"), perms("payroll", "delete"))
        self.assertEqual(perms("payroll", "reject"), perms("payroll", "delete"))
        self.assertEqual(perms("payroll", "makePayment"), perms("payroll", "create"))

    def test_django_permission_names_are_unique(self):
        """Even the actions sharing an identifier keep a distinct django name."""
        seen = {}
        for entity, actions in DJANGO_PERMS.items():
            for action, (name, _) in actions.items():
                seen.setdefault(name, []).append(f"{entity}.{action}")
        shared = {name: who for name, who in seen.items() if len(who) > 1}
        self.assertEqual(shared, {})

    def test_django_permission_names_use_the_app_label(self):
        """The app_label in this assembly is `payroll`, not the pip package name."""
        for entity, actions in DJANGO_PERMS.items():
            for action, (name, _) in actions.items():
                with self.subTest(entity=entity, action=action):
                    self.assertTrue(name.startswith("payroll."))

    def test_unknown_entity_or_action_raises(self):
        with self.assertRaises(KeyError):
            perms("nosuchentity", "query")
        with self.assertRaises(KeyError):
            perms("payroll", "nosuchaction")
        with self.assertRaises(KeyError):
            django_perms("payroll", "nosuchaction")

    # --- the access points through the models -----------------------------
    def test_models_expose_every_action_of_their_entity(self):
        for model, entity in ((Payroll, "payroll"),
                              (PaymentPoint, "paymentPoint"),
                              (CsvReconciliationUpload, "csvReconciliation")):
            for action in DJANGO_PERMS[entity]:
                with self.subTest(model=model.__name__, action=action):
                    self.assertEqual(
                        model.get_rights(action), configured_perms(entity, action)
                    )
                    self.assertTrue(model.get_rights(action))

    def test_models_return_none_for_an_undeclared_action(self):
        """None means "no rule": the caller must fail closed."""
        self.assertIsNone(Payroll.get_rights("nosuchaction"))
        self.assertIsNone(PaymentPoint.get_rights("nosuchaction"))
        # No mutation modifies a payroll: "update" is not declared.
        self.assertIsNone(Payroll.get_rights("update"))

    def test_benefit_consumption_takes_the_payroll_rights(self):
        """
        A benefit has no rights of its own: it is only read and deleted through its
        payroll, and 202001/202004 really are what the call sites check.
        """
        self.assertEqual(
            BenefitConsumption.get_rights("query"), configured_perms("payroll", "query")
        )
        self.assertEqual(
            BenefitConsumption.get_rights("delete"),
            configured_perms("payroll", "delete"),
        )

    def test_benefit_consumption_declares_no_scope_parent(self):
        """
        `scope_parent = "payroll"` would be wrong: there is no `payroll` relation on
        this model, the link goes through PayrollBenefitConsumption. `scope_parent_of`
        would return None after logging an error.
        """
        from core.rights_scope import scope_parent_of

        self.assertIsNone(getattr(BenefitConsumption, "scope_parent", None))
        self.assertIsNone(scope_parent_of(BenefitConsumption))

    def test_sub_resources_resolve_to_their_owner(self):
        from core.rights_scope import model_rights, scope_parent_of

        self.assertEqual(scope_parent_of(PayrollBill), Payroll)
        self.assertEqual(scope_parent_of(PayrollBenefitConsumption), Payroll)
        self.assertEqual(scope_parent_of(BenefitAttachment), BenefitConsumption)
        for model in (PayrollBill, PayrollBenefitConsumption, BenefitAttachment):
            with self.subTest(model=model.__name__):
                self.assertEqual(
                    model_rights(model, "delete"), configured_perms("payroll", "delete")
                )

    def test_csv_reconciliation_does_not_inherit_the_payroll_rights(self):
        """A distinct 206xxx block: no falling back onto the payroll's right."""
        from core.rights_scope import model_rights

        self.assertIsNone(getattr(CsvReconciliationUpload, "scope_parent", None))
        self.assertIsNone(model_rights(CsvReconciliationUpload, "delete"))

    def test_configured_reads_the_configured_value_not_the_declared_default(self):
        """
        ModuleConfiguration may override a right; a check must read the configured
        value, where `perms()` returns the declared default.
        """
        original = PayrollConfig.gql_payroll_search_perms
        try:
            PayrollConfig.gql_payroll_search_perms = ["999999"]
            self.assertEqual(Payroll.get_rights("query"), ["999999"])
            self.assertEqual(perms("payroll", "query"), ["202001"])
        finally:
            PayrollConfig.gql_payroll_search_perms = original

    def test_ids_match_permissions_map(self):
        """The assembly's rights map must carry the same integers."""
        mapping = _load_permissions_map()
        if mapping is None:
            self.skipTest("permissions_map.json not found in this assembly")
        for key, right_id in EXPECTED_MAP_ENTRIES.items():
            with self.subTest(key=key):
                self.assertEqual(str(mapping.get(key)), right_id)
        declared_ids = {
            str(right_id)
            for actions in DJANGO_PERMS.values()
            for _, right_id in actions.values()
        }
        self.assertEqual(set(EXPECTED_MAP_ENTRIES.values()), declared_ids)
