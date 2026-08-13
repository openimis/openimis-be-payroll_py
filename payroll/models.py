from django.db import models
from django.utils.translation import gettext as _

from core.models import HistoryModel, HistoryBusinessModel, User, UUIDModel, ObjectMutation, MutationLog
from core.fields import DateField
from invoice.models import Bill
from location.models import Location
from payment_cycle.models import PaymentCycle
from contribution_plan.models import PaymentPlan
from individual.models import Individual
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType



class PayrollStatus(models.TextChoices):
    PENDING_APPROVAL = "PENDING_APPROVAL", _("PENDING_APPROVAL")
    APPROVE_FOR_PAYMENT = "APPROVE_FOR_PAYMENT", _("APPROVE_FOR_PAYMENT")
    REJECTED = "REJECTED", _("REJECTED")
    RECONCILED = "RECONCILED", _("RECONCILED")


class BenefitConsumptionStatus(models.TextChoices):
    ACCEPTED = "ACCEPTED", _("ACCEPTED")
    CREATED = "CREATED", _("CREATED")
    APPROVE_FOR_PAYMENT = "APPROVE_FOR_PAYMENT", _("APPROVE_FOR_PAYMENT")
    REJECTED = "REJECTED", _("REJECTED")
    DUPLICATE = "DUPLICATE", _("DUPLICATE")
    RECONCILED = "RECONCILED", _("RECONCILED")
    PENDING_DELETION = "PENDING_DELETION", _("PENDING_DELETION")


class PaymentPoint(HistoryModel):
    name = models.CharField(max_length=255)
    location = models.ForeignKey(Location, models.DO_NOTHING)
    ppm = models.ForeignKey(User, models.DO_NOTHING, blank=True, null=True)


class Payroll(HistoryBusinessModel):
    name = models.CharField(max_length=255, blank=False, null=False)
    payment_plan = models.ForeignKey(PaymentPlan, on_delete=models.DO_NOTHING, blank=True, null=True)
    payment_cycle = models.ForeignKey(PaymentCycle, on_delete=models.DO_NOTHING, blank=True, null=True)
    payment_point = models.ForeignKey(PaymentPoint, on_delete=models.DO_NOTHING, blank=True, null=True)
    status = models.CharField(
        max_length=100, choices=PayrollStatus.choices, default=PayrollStatus.PENDING_APPROVAL, null=False
    )
    payment_method = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"Payroll {self.name} - {self.uuid}"


class PayrollBill(HistoryModel):
    # 1:n it is ensured by the service
    payroll = models.ForeignKey(Payroll, on_delete=models.DO_NOTHING)
    bill = models.ForeignKey(Bill, on_delete=models.DO_NOTHING)


class PaymentAdaptorHistory(HistoryModel):
    payroll = models.ForeignKey(Payroll, on_delete=models.DO_NOTHING)
    total_amount = models.CharField(max_length=255, blank=True, null=True)
    bills_ids = models.JSONField()


class BenefitConsumption(HistoryBusinessModel):
    recipient_id = models.CharField(max_length=255, blank=True, null=True)
    recipient_type = models.ForeignKey(
        ContentType,
        on_delete=models.DO_NOTHING,
        null=True,
        unique=False,
        default=ContentType.objects.get_for_model(Individual).id,
        related_name='benefit_consumption_recipient_type'
    )
    recipient = GenericForeignKey('recipient_type', 'recipient_id') 
    benefit_plan_id = models.CharField(max_length=255, blank=True, null=True)
    benefit_plan_type = models.ForeignKey(
        ContentType, 
        on_delete=models.DO_NOTHING,
        null=True,
        unique=False,
        related_name='benefit_consumption_benefit_plan_type'
    )
    benefit_plan = GenericForeignKey('benefit_plan_type', 'benefit_plan_id') 
    photo = models.TextField(blank=True, null=True)
    code = models.CharField(max_length=255, blank=False, null=False)
    date_due = DateField(db_column='DateDue', null=True)
    receipt = models.CharField(db_column='Receipt', max_length=255, null=True, blank=True)
    amount = models.DecimalField(db_column='Amount', max_digits=18, decimal_places=2, null=True)
    type = models.CharField(db_column='Type', max_length=255, null=True)
    status = models.CharField(
        max_length=100, choices=BenefitConsumptionStatus.choices, default=BenefitConsumptionStatus.ACCEPTED, null=False
    )
    
    def __str__(self):
        return f"Benefit Consumption {self.code} - {self.receipt} - {self.amount}"
    
    
    @property
    def individual(self):
        if self.recipient_type.model == 'individual':
            return self.recipient
        return None

    @individual.setter
    def individual(self, value):
        if value is None:
            self.recipient = None
            self.recipient_type = None
            self.recipient_id = None
        else:
            self.recipient = value
            self.recipient_type = ContentType.objects.get_for_model(value)
            self.recipient_id = value.id

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._original_recipient = instance.recipient
        return instance

    def save(self, *args, **kwargs):
        if self._state.adding or self.recipient != self._original_recipient:
            self.individual = self.recipient if isinstance(self.recipient, Individual) else None
        super().save(*args, **kwargs)


class BenefitAttachment(HistoryBusinessModel):
    benefit = models.ForeignKey(BenefitConsumption, on_delete=models.DO_NOTHING)
    bill = models.ForeignKey(Bill, on_delete=models.DO_NOTHING)


class PayrollBenefitConsumption(HistoryModel):
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


class PayrollMutation(UUIDModel, ObjectMutation):
    payroll = models.ForeignKey(Payroll, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(
        MutationLog, models.DO_NOTHING, related_name='payroll')
