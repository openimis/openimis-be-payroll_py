from django.db import models
from django.utils.translation import gettext as _

from core.models import HistoryModel, HistoryBusinessModel, User, UUIDModel, ObjectMutation, MutationLog
from core.fields import DateField
from invoice.models import Bill
from location.models import Location
from payment_cycle.models import PaymentCycle
from contribution_plan.models import PaymentPlan
from individual.models import Individual
from core.models import LocationScope, ParentScope


class PayrollStatus(models.TextChoices):
    GENERATING = "GENERATING", _("GENERATING")
    PENDING_APPROVAL = "PENDING_APPROVAL", _("PENDING_APPROVAL")
    APPROVE_FOR_PAYMENT = "APPROVE_FOR_PAYMENT", _("APPROVE_FOR_PAYMENT")
    REJECTED = "REJECTED", _("REJECTED")
    RECONCILED = "RECONCILED", _("RECONCILED")
    FAILED = "FAILED", _("FAILED")


class BenefitConsumptionStatus(models.TextChoices):
    ACCEPTED = "ACCEPTED", _("ACCEPTED")
    CREATED = "CREATED", _("CREATED")
    APPROVE_FOR_PAYMENT = "APPROVE_FOR_PAYMENT", _("APPROVE_FOR_PAYMENT")
    REJECTED = "REJECTED", _("REJECTED")
    DUPLICATE = "DUPLICATE", _("DUPLICATE")
    RECONCILED = "RECONCILED", _("RECONCILED")
    PENDING_DELETION = "PENDING_DELETION", _("PENDING_DELETION")


class PaymentPoint(HistoryModel):
    row_scope = LocationScope("location")

    name = models.CharField(max_length=255)
    location = models.ForeignKey(Location, models.DO_NOTHING)
    ppm = models.ForeignKey(User, models.DO_NOTHING, blank=True, null=True)

    @classmethod
    def get_rights(cls, action):
        """
        The rights governing an action on a payment point, for GraphQL, REST and FHIR.

        Redeclares nothing: the rights table is `payroll.apps.DJANGO_PERMS`, by entity
        then by action, and `configured_perms` reads the *configured* value there - the
        one ModuleConfiguration may have overridden - and not the declared default. The
        read happens inside the method, never at import time: the `_perms` keys only
        hold their value after `ready()`, and an empty list is granted to everybody by
        `has_perms`.
        """
        from payroll.apps import configured_perms

        return configured_perms("paymentPoint", action)


class Payroll(HistoryBusinessModel):
    name = models.CharField(max_length=255, blank=False, null=False)
    payment_plan = models.ForeignKey(PaymentPlan, on_delete=models.DO_NOTHING, blank=True, null=True)
    payment_cycle = models.ForeignKey(PaymentCycle, on_delete=models.DO_NOTHING, blank=True, null=True)
    payment_point = models.ForeignKey(PaymentPoint, on_delete=models.DO_NOTHING, blank=True, null=True)
    status = models.CharField(
        max_length=100, choices=PayrollStatus.choices, default=PayrollStatus.GENERATING, null=False
    )
    payment_method = models.CharField(max_length=255, blank=True, null=True)

    @classmethod
    def get_rights(cls, action):
        """
        The rights governing an action on a payroll, for GraphQL, REST and FHIR.

        An access point only: the table is `payroll.apps.DJANGO_PERMS`. Every action of
        the entity is available, not only the four canonical ones - "close", "reject",
        "makePayment" are state transitions and are asked for under those names. Three
        of them currently share the delete or the create integer; that is declared in
        DJANGO_PERMS, not here.
        """
        from payroll.apps import configured_perms

        return configured_perms("payroll", action)

    def __str__(self):
        return f"Payroll {self.name} - {self.uuid}"


class PayrollBill(HistoryModel):
    # The row attaching an invoice to a payroll: nobody holds a right on it
    # separately, and handling it means handling the payroll. Of the two foreign keys,
    # `payroll` is the owner - the invoice exists without this attachment and has its
    # own rights in `invoice`.
    scope_parent = "payroll"

    # 1:n it is ensured by the service
    payroll = models.ForeignKey(Payroll, on_delete=models.DO_NOTHING)
    bill = models.ForeignKey(Bill, on_delete=models.DO_NOTHING)


class PaymentAdaptorHistory(HistoryModel):
    # The trace of a call to the gateway for a given payroll: a sub-resource of the
    # payroll, and its only foreign key.
    scope_parent = "payroll"

    payroll = models.ForeignKey(Payroll, on_delete=models.DO_NOTHING)
    total_amount = models.CharField(max_length=255, blank=True, null=True)
    bills_ids = models.JSONField()


class BenefitConsumption(HistoryBusinessModel):
    row_scope = ParentScope("individual")

    individual = models.ForeignKey(Individual, on_delete=models.DO_NOTHING)
    photo = models.TextField(blank=True, null=True)
    code = models.CharField(max_length=255, blank=True, default='')
    date_due = DateField(db_column='DateDue', null=True)
    receipt = models.CharField(db_column='Receipt', max_length=255, null=True, blank=True)
    amount = models.DecimalField(db_column='Amount', max_digits=18, decimal_places=2, null=True)
    type = models.CharField(db_column='Type', max_length=255, null=True)
    status = models.CharField(
        max_length=100, choices=BenefitConsumptionStatus.choices, default=BenefitConsumptionStatus.ACCEPTED, null=False
    )

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        code_was_empty = not self.code
        result = super().save(*args, **kwargs)
        if is_new and code_was_empty:
            self.refresh_from_db(fields=['code'])
            # Patch history record with DB-assigned code.
            latest = self.history.filter(history_type='+').order_by('-history_date').values('history_id').first()
            if latest:
                self.history.model.objects.filter(history_id=latest['history_id']).update(code=self.code)
        return result

    # No rights of its own, and no `scope_parent` either: a benefit has no relation
    # named `payroll` for `core.rights_scope` to walk up - the link goes through the
    # PayrollBenefitConsumption table. Declaring `scope_parent = "payroll"` would make
    # `scope_parent_of` log an error and return None, so `has_model_right` would refuse
    # everything. The attachment is therefore explicit below: the payroll's right
    # really is what the two call sites already check (202001 for reading, 202004 for
    # deleting).
    @classmethod
    def get_rights(cls, action):
        from payroll.apps import configured_perms

        return configured_perms("payroll", action)

    def __str__(self):
        return f"Benefit Consumption {self.code} - {self.receipt} - {self.amount}"


class BenefitAttachment(HistoryBusinessModel):
    # The attachment of an invoice to a benefit. Of the two foreign keys, `benefit` is
    # the owner: the invoice has its own rights in `invoice`, and the attachment exists
    # only for the benefit. The chain then walks from BenefitConsumption up to the
    # payroll.
    scope_parent = "benefit"

    benefit = models.ForeignKey(BenefitConsumption, on_delete=models.DO_NOTHING)
    bill = models.ForeignKey(Bill, on_delete=models.DO_NOTHING)


class PayrollBenefitConsumption(HistoryModel):
    # A payroll line. Of the two foreign keys, `payroll` is the owner: the benefit
    # predates the payroll and may change payroll, the line may not.
    scope_parent = "payroll"

    # 1:n it is ensured by the service
    payroll = models.ForeignKey(Payroll, on_delete=models.DO_NOTHING)
    benefit = models.ForeignKey(BenefitConsumption, on_delete=models.DO_NOTHING)


class CsvReconciliationUpload(HistoryModel):
    class Status(models.TextChoices):
        TRIGGERED = 'TRIGGERED', _('Triggered')
        IN_PROGRESS = 'IN_PROGRESS', _('In progress')
        SUCCESS = 'SUCCESS', _('Success')
        PARTIAL_SUCCESS = 'PARTIAL_SUCCESS', _('Partial Success')
        WAITING_FOR_VERIFICATION = 'WAITING_FOR_VERIFICATION', _('WAITING_FOR_VERIFICATION')
        FAIL = 'FAIL', _('Fail')

    payroll = models.ForeignKey(Payroll, models.DO_NOTHING, null=True, blank=True)
    status = models.CharField(max_length=255, choices=Status.choices, default=Status.TRIGGERED)
    error = models.JSONField(blank=True, default=dict)
    file_name = models.CharField(max_length=255, null=True, blank=True)

    @classmethod
    def get_rights(cls, action):
        """
        The CSV reconciliation's rights: block 206xxx, distinct from the payroll's.

        No `scope_parent` to `payroll`: this entity has rights of its own, and falling
        back onto the payroll's would give "delete a payroll" an authority over
        reconciliations that nobody intended. An undeclared action therefore returns
        None, and the caller fails closed.
        """
        from payroll.apps import configured_perms

        return configured_perms("csvReconciliation", action)


class PayrollMutation(UUIDModel, ObjectMutation):
    payroll = models.ForeignKey(Payroll, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(
        MutationLog, models.DO_NOTHING, related_name='payroll')
