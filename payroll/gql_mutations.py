import graphene
from gettext import gettext as _
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import transaction

from core.gql.gql_mutations.base_mutation import BaseHistoryModelCreateMutationMixin, BaseMutation, \
    BaseHistoryModelUpdateMutationMixin, BaseHistoryModelDeleteMutationMixin
from core.schema import OpenIMISMutation
from payroll.apps import PayrollConfig
from payroll.models import BenefitConsumption, PaymentPoint, Payroll, PayrollMutation
from payroll.services import PaymentPointService, PayrollService, BenefitConsumptionService


class CreatePaymentPointInputType(OpenIMISMutation.Input):
    name = graphene.String(required=True, max_length=255)
    location_id = graphene.Int(required=True)
    ppm_id = graphene.UUID(required=True)


class UpdatePaymentPointInputType(CreatePaymentPointInputType):
    id = graphene.UUID(required=True)


class DeletePaymentPointInputType(OpenIMISMutation.Input):
    ids = graphene.List(graphene.UUID, required=True)


class UpdatePaymentGatewayConfigInputType(OpenIMISMutation.Input):
    base_url = graphene.String(required=True, max_length=255)
    api_key = graphene.String(required=True, max_length=255)
    timeout = graphene.Int(required=True)


class CreatePayrollInput(OpenIMISMutation.Input):

    name = graphene.String(required=True, max_length=255)
    payment_plan_id = graphene.UUID(required=True)
    payment_point_id = graphene.UUID(required=False)
    payment_cycle_id = graphene.UUID(required=False)
    payment_method = graphene.String(required=True, max_length=255)
    from_failed_invoices_payroll_id = graphene.UUID(required=False)

    date_valid_from = graphene.Date(required=False)
    date_valid_to = graphene.Date(required=False)
    json_ext = graphene.JSONString(required=False)


class DeletePayrollInputType(DeletePaymentPointInputType):
    pass


class ClosePayrollInputType(DeletePaymentPointInputType):
    pass


class CreatePaymentPointMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "CreatePaymentPointMutation"
    _mutation_module = PayrollConfig.name
    _model = PaymentPoint

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.id or not user.has_perms(
                PayrollConfig.gql_payment_point_create_perms):
            raise ValidationError(_("mutation.authentication_required"))

    @classmethod
    def _mutate(cls, user, **data):
        data.pop('client_mutation_id', None)
        data.pop('client_mutation_label', None)

        service = PaymentPointService(user)
        service.create(data)

    class Input(CreatePaymentPointInputType):
        pass


class UpdatePaymentPointMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "UpdatePaymentPointMutation"
    _mutation_module = PayrollConfig.name
    _model = PaymentPoint

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.id or not user.has_perms(
                PayrollConfig.gql_payment_point_update_perms):
            raise ValidationError(_("mutation.authentication_required"))

    @classmethod
    def _mutate(cls, user, **data):
        data.pop('client_mutation_id', None)
        data.pop('client_mutation_label', None)

        service = PaymentPointService(user)
        service.update(data)

    class Input(UpdatePaymentPointInputType):
        pass


class DeletePaymentPointMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "DeletePaymentPointMutation"
    _mutation_module = PayrollConfig.name
    _model = PaymentPoint

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.id or not user.has_perms(
                PayrollConfig.gql_payment_point_delete_perms):
            raise ValidationError(_("mutation.authentication_required"))

    @classmethod
    def _mutate(cls, user, **data):
        data.pop('client_mutation_id', None)
        data.pop('client_mutation_label', None)

        service = PaymentPointService(user)

        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for id in ids:
                    service.delete({'id': id, 'user': user})

    class Input(DeletePaymentPointInputType):
        pass


class CreatePayrollMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "CreatePayrollMutation"
    _mutation_module = "payroll"
    _model = Payroll

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.has_perms(
                PayrollConfig.gql_payroll_create_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        client_mutation_id = data.pop('client_mutation_id', None)
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = PayrollService(user)
        response = service.create(data)
        if client_mutation_id and response['success']:
            payroll_id = response['data']['id']
            payroll = Payroll.objects.get(id=payroll_id)
            PayrollMutation.object_mutated(
                user, client_mutation_id=client_mutation_id, payroll=payroll
            )
        if not response['success']:
            return response
        return None

    class Input(CreatePayrollInput):
        pass


class DeletePayrollMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "DeletePayrollMutation"
    _mutation_module = "payroll"
    _model = Payroll

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.has_perms(
                PayrollConfig.gql_payroll_delete_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = PayrollService(user)
        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for id in ids:
                    service.delete({'id': id, 'user': user})

    class Input(DeletePayrollInputType):
        pass


class ClosePayrollMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "ClosePayrollMutation"
    _mutation_module = "payroll"
    _model = Payroll

    @classmethod
    def _validate_mutation(cls, user, **data):
        # Closing a payroll is not deleting it. Same id as delete for now, see
        # PayrollConfig - only the name changes here.
        if type(user) is AnonymousUser or not user.has_perms(
                PayrollConfig.gql_payroll_close_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = PayrollService(user)
        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for id in ids:
                    service.close_payroll({'id': id})

    class Input(DeletePayrollInputType):
        pass


class MakePaymentForPayrollMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "MakePaymentForPayrollMutation"
    _mutation_module = "payroll"
    _model = Payroll

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        # This one moves money: it is a disbursement, not the creation of a payroll.
        # Same id as create for now, see PayrollConfig.
        if not user.has_perms(PayrollConfig.gql_payroll_make_payment_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = PayrollService(user)
        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for id in ids:
                    service.make_payment_for_payroll({'id': id})

    class Input(DeletePayrollInputType):
        pass


class RejectPayrollMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "RejectPayrollMutation"
    _mutation_module = "payroll"
    _model = Payroll

    @classmethod
    def _validate_mutation(cls, user, **data):
        # Rejecting a payroll is not deleting it. Same id as delete for now, see
        # PayrollConfig - only the name changes here.
        if type(user) is AnonymousUser or not user.has_perms(
                PayrollConfig.gql_payroll_reject_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = PayrollService(user)
        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for id in ids:
                    service.reject_approved_payroll({'id': id})

    class Input(DeletePayrollInputType):
        pass


class DeleteBenefitConsumptionMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "DeleteBenefitConsumptionMutation"
    _mutation_module = "payroll"
    # This mutation deletes BenefitConsumption rows, not Payroll ones; `_model`
    # announced the latter. Without functional effect - `_model` is only read by the
    # mixin's `__delete_single_obj` and `_object_not_exist_exception`, and `_mutate` is
    # rewritten in full below and delegates to BenefitConsumptionService - but wrong
    # for anyone looking up an action's right through its model
    # (`core.rights_scope.has_model_right`).
    _model = BenefitConsumption

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.has_perms(
                PayrollConfig.gql_payroll_delete_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = BenefitConsumptionService(user)
        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for id in ids:
                    service.delete({'id': id})

    class Input(DeletePayrollInputType):
        pass


class RetriggerPayrollMutation(BaseMutation):
    _mutation_class = "RetriggerPayrollMutation"
    _mutation_module = "payroll"
    _model = Payroll

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.has_perms(
                PayrollConfig.gql_payroll_create_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        data.pop('client_mutation_id', None)
        data.pop('client_mutation_label', None)

        service = PayrollService(user)
        response = service.retrigger_creation(data)
        if not response.get('success', True):
            return response
        return None

    class Input(OpenIMISMutation.Input):
        id = graphene.UUID(required=True)
