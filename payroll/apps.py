import logging
import os

from django.apps import AppConfig

from core.bootstrap import rerun_after_migrate, skip_without_database
from core.custom_filters import CustomFilterRegistryPoint
from core.rights_declaration import RightsDeclaration
from payroll.payments_registry import PaymentsMethodRegistryPoint

logger = logging.getLogger(__name__)

MODULE_NAME = 'payroll'

# Rights, by entity then by action. The integers are those already deployed: none has
# moved here.
#
# Two identifier sharings, deliberate and carried over as they stand:
#
#   * close, reject and delete are all 202004. Closing a payroll is not deleting it,
#     nor is rejecting it, but the integer all three check today really is the delete
#     one. Only the django names separate them, ready for the day each has its own
#     integer.
#   * makePayment is 202002, the create integer. A disbursement is not the creation of
#     a payroll; same situation.
#
# The sharing is written down here rather than merely endured: `RightPermission.right_id`
# is indexed but not unique, so several names may point at the same integer. Splitting
# them for real requires new integers *and* a migration granting them to the roles
# holding the old one - without which the split withdraws accesses. 202003 stays free
# in the block and is the natural place for a future "update payroll" (sequence 01
# query / 02 create / 03 update / 04 delete): that is why the gateway took 202005 and
# not it.
#
# close / reject / makePayment / reconcile are business actions and keep their name:
# having forced them into delete and create is exactly what produced the sharings
# above.
DJANGO_PERMS = {
    "paymentPoint": {
        "query": ("payroll.view_paymentpoint", 201001),
        "create": ("payroll.add_paymentpoint", 201002),
        "update": ("payroll.change_paymentpoint", 201003),
        "delete": ("payroll.delete_paymentpoint", 201004),
    },
    "payroll": {
        "query": ("payroll.view_payroll", 202001),
        "create": ("payroll.add_payroll", 202002),
        # No "update" action: no mutation modifies a payroll, it only moves forward
        # through its state transitions below.
        "delete": ("payroll.delete_payroll", 202004),
        "close": ("payroll.close_payroll", 202004),
        "reject": ("payroll.reject_payroll", 202004),
        "makePayment": ("payroll.make_payment_payroll", 202002),
    },
    # Reading this configuration returns `payment_gateway_api_key`, the external
    # gateway's credential: that is a right of its own, revocable on its own. There is
    # no model behind it - it is a ModuleConfiguration entry - so the django name is
    # purely declarative, like the others.
    "paymentGatewayConfig": {
        "query": ("payroll.view_paymentgatewayconfig", 202005),
    },
    "csvReconciliation": {
        "query": ("payroll.view_csvreconciliationupload", 206001),
        # 206002 carries the name "create" in the deployed config, but what it opens
        # is the upload of the reconciliation file: the CsvReconciliationUpload row is
        # only its trace, the effect bears on the payroll's benefits. Hence a business
        # action, and not "create".
        "reconcile": ("payroll.reconcile_csvreconciliationupload", 206002),
    },
}

_PERM_CFG = {
    "gql_payment_point_search_perms": ("paymentPoint", "query"),
    "gql_payment_point_create_perms": ("paymentPoint", "create"),
    "gql_payment_point_update_perms": ("paymentPoint", "update"),
    "gql_payment_point_delete_perms": ("paymentPoint", "delete"),
    "gql_payroll_search_perms": ("payroll", "query"),
    "gql_payroll_create_perms": ("payroll", "create"),
    "gql_payroll_delete_perms": ("payroll", "delete"),
    "gql_payroll_close_perms": ("payroll", "close"),
    "gql_payroll_reject_perms": ("payroll", "reject"),
    "gql_payroll_make_payment_perms": ("payroll", "makePayment"),
    "gql_payment_gateway_config_perms": ("paymentGatewayConfig", "query"),
    "gql_csv_reconciliation_search_perms": ("csvReconciliation", "query"),
    # A deployed key, so not renamed: it points at the "reconcile" action declared
    # above.
    "gql_csv_reconciliation_create_perms": ("csvReconciliation", "reconcile"),
}

RIGHTS = RightsDeclaration(MODULE_NAME, DJANGO_PERMS, _PERM_CFG)

perms = RIGHTS.perms
django_perms = RIGHTS.django_perm_names
configured_perms = RIGHTS.configured
require = RIGHTS.require


DEFAULT_CONFIG = {
    "payroll_accept_event": "payroll.accept_payroll",
    "payroll_reconciliation_event": "payroll.payroll_reconciliation",
    "payroll_reject_event": "payroll.payroll_reject",
    "csv_reconciliation_field_mapping": {
        'payrollbenefitconsumption__payroll__name': 'Payroll Name',
        'payrollbenefitconsumption__payroll__status': 'Payroll Status',
        'individual__first_name': 'First Name',
        'individual__last_name': 'Last Name',
        'individual__dob': 'Date of Birth',
        'code': 'Code',
        'status': 'Status',
        'amount': 'Amount',
        'type': 'Type',
        'receipt': 'Receipt',
    },
    "csv_reconciliation_status_column": "Status",
    "csv_reconciliation_paid_extra_field": "Paid",
    "csv_reconciliation_receipt_column": "receipt",
    "csv_reconciliation_errors_column": "errors",
    "csv_reconciliation_code_column": "code",
    "csv_reconciliation_paid_yes": "Yes",
    "csv_reconciliation_paid_no": "No",
    "payroll_delete_event": "payroll.payroll_delete",
    "benefit_delete_event": "payroll.benefit_delete",

    "gateway_base_url": "http://41.175.18.170:8070/api/mobile/v1/",
    "endpoint_payment": "mock/payment",
    "endpoint_reconciliation": "mock/reconciliation",
    "payment_gateway_api_key": os.getenv('PAYMENT_GATEWAY_API_KEY'),
    "payment_gateway_basic_auth_username": os.getenv('PAYMENT_GATEWAY_BASIC_AUTH_USERNAME'),
    "payment_gateway_basic_auth_password": os.getenv('PAYMENT_GATEWAY_BASIC_AUTH_PASSWORD'),
    "payment_gateway_timeout": 5,
    "payment_gateway_auth_type": "basic",  # can be 'token' or 'basic'
    "payment_gateway_class": "payroll.payment_gateway.MockedPaymentGatewayConnector",
    "receipt_length": 8,
    "benefit_code_pattern": "BEN-[YY]-[SEQ:10]",
    "bulk_create_batch_size": 500,
}


class PayrollConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = MODULE_NAME

    # Rights: constants, no longer overridable. They go neither through DEFAULT_CFG
    # nor through ready(): `ModuleConfiguration.get_or_default` now ignores any
    # `_perms` key stored in the database. The values come from DJANGO_PERMS, written
    # once only.
    gql_payment_point_search_perms = RIGHTS.perms("paymentPoint", "query")
    gql_payment_point_create_perms = RIGHTS.perms("paymentPoint", "create")
    gql_payment_point_update_perms = RIGHTS.perms("paymentPoint", "update")
    gql_payment_point_delete_perms = RIGHTS.perms("paymentPoint", "delete")

    gql_payroll_search_perms = RIGHTS.perms("payroll", "query")
    gql_payroll_create_perms = RIGHTS.perms("payroll", "create")
    gql_payroll_delete_perms = RIGHTS.perms("payroll", "delete")
    gql_payroll_close_perms = RIGHTS.perms("payroll", "close")
    gql_payroll_reject_perms = RIGHTS.perms("payroll", "reject")
    gql_payroll_make_payment_perms = RIGHTS.perms("payroll", "makePayment")

    gql_payment_gateway_config_perms = RIGHTS.perms("paymentGatewayConfig", "query")

    gql_csv_reconciliation_search_perms = RIGHTS.perms("csvReconciliation", "query")
    gql_csv_reconciliation_create_perms = RIGHTS.perms("csvReconciliation", "reconcile")

    payroll_accept_event = None
    payroll_reconciliation_event = None
    payroll_reject_event = None
    csv_reconciliation_field_mapping = None
    csv_reconciliation_status_column = None
    csv_reconciliation_paid_extra_field = None
    csv_reconciliation_receipt_column = None
    csv_reconciliation_errors_column = None
    csv_reconciliation_code_column = None
    csv_reconciliation_paid_yes = None
    csv_reconciliation_paid_no = None
    payroll_delete_event = None
    benefit_delete_event = None

    gateway_base_url = None
    endpoint_payment = None
    endpoint_reconciliation = None
    payment_gateway_api_key = None
    payment_gateway_basic_auth_username = None
    payment_gateway_basic_auth_password = None
    payment_gateway_timeout = None
    payment_gateway_auth_type = None
    payment_gateway_class = None
    receipt_length = None
    benefit_code_pattern = None
    bulk_create_batch_size = None
    benefit_trigger_synced = False

    def ready(self):
        from core.models import ModuleConfiguration

        cfg = ModuleConfiguration.get_or_default(self.name, DEFAULT_CONFIG)
        self.__load_config(cfg)
        self.__register_filters_and_payment_methods()
        self._sync_benefit_trigger()
        self._connect_migrate_signal()
        self._connect_config_signal()

    @classmethod
    def __load_config(cls, cfg):
        """
        Load all config fields that match current AppConfig class fields, all custom fields have to be loaded separately
        """
        for field in cfg:
            if hasattr(PayrollConfig, field):
                setattr(PayrollConfig, field, cfg[field])

    def __register_filters_and_payment_methods(cls):
        from social_protection.custom_filters import BenefitPlanCustomFilterWizard
        CustomFilterRegistryPoint.register_custom_filters(
            module_name=cls.name,
            custom_filter_class_list=[BenefitPlanCustomFilterWizard]
        )

        from payroll.strategies import (
            StrategyOfflinePayment,
            StrategyOnlinePayment
        )
        PaymentsMethodRegistryPoint.register_payment_method(
            payment_method_class_list=[
                StrategyOfflinePayment(),
                StrategyOnlinePayment(),
            ]
        )

    # ready() runs before migrations, so on a fresh database the table the trigger
    # attaches to does not exist yet; _connect_migrate_signal retries it then.
    @skip_without_database("benefit code trigger sync", logger)
    def _sync_benefit_trigger(self):
        PayrollConfig.benefit_trigger_synced = False
        from payroll.models import BenefitConsumption
        from invoice.trigger_sync import sync_trigger
        sync_trigger(
            model=BenefitConsumption,
            sequence_name='benefit_code_seq',
            trigger_name='benefit_code_trigger',
            code_column='code',
            pattern=self.benefit_code_pattern or DEFAULT_CONFIG['benefit_code_pattern'],
            pg_function_name='set_benefit_code',
        )
        PayrollConfig.benefit_trigger_synced = True

    def _connect_migrate_signal(self):
        rerun_after_migrate(self, self._sync_benefit_trigger, "payroll.benefit_code_trigger_post_migrate")

    def _connect_config_signal(self):
        from django.db.models.signals import post_save
        from core.models import ModuleConfiguration
        post_save.connect(
            self._on_config_change, sender=ModuleConfiguration,
            dispatch_uid='payroll.benefit_code_trigger_sync',
        )

    @staticmethod
    def _on_config_change(sender, instance, **kwargs):
        import json
        if instance.module != MODULE_NAME or instance.layer != 'be':
            return
        try:
            cfg = json.loads(instance.config) if isinstance(instance.config, str) else instance.config
            pattern = cfg.get('benefit_code_pattern') or DEFAULT_CONFIG['benefit_code_pattern']
            from payroll.models import BenefitConsumption
            from invoice.trigger_sync import sync_trigger, validate_pattern
            validate_pattern(pattern)
            PayrollConfig.benefit_code_pattern = pattern
            sync_trigger(
                model=BenefitConsumption,
                sequence_name='benefit_code_seq',
                trigger_name='benefit_code_trigger',
                code_column='code',
                pattern=pattern,
                pg_function_name='set_benefit_code',
            )
            PayrollConfig.benefit_trigger_synced = True
            logger.info(f"Benefit trigger updated after config change (pattern: {pattern})")
        except Exception as e:
            PayrollConfig.benefit_trigger_synced = False
            logger.error(f"Failed to sync benefit trigger after config change: {e}", exc_info=True)

    @staticmethod
    def get_payroll_payment_file_path(payroll_id, file_name=None):
        if file_name:
            return f"csv_reconciliation/payroll_{payroll_id}/{file_name}"
        return f"csv_reconciliation/payroll_{payroll_id}"
