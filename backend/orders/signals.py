from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    OrderCustomerNotification,
    OrderItemEvent,
    OrderSupportMessage,
)


def notification_type_for_event(event):
    if event.event_type in {
        OrderItemEvent.EVENT_TYPE_PRICE_CHANGE_REQUESTED,
        OrderItemEvent.EVENT_TYPE_ETA_CHANGE_REQUESTED,
        OrderItemEvent.EVENT_TYPE_CHANGE_REQUESTED,
    }:
        return OrderCustomerNotification.TYPE_ACTION_REQUIRED

    if event.event_type == OrderItemEvent.EVENT_TYPE_STATUS_CHANGED:
        return OrderCustomerNotification.TYPE_ORDER_UPDATE

    return OrderCustomerNotification.TYPE_NOTICE


@receiver(post_save, sender=OrderItemEvent)
def create_notification_for_order_item_event(sender, instance, created, **kwargs):
    if not created:
        return

    if not instance.visible_to_customer:
        return

    if instance.event_type == OrderItemEvent.EVENT_TYPE_CREATED:
        return

    if OrderCustomerNotification.objects.filter(event=instance).exists():
        return

    OrderCustomerNotification.objects.create(
        order=instance.item.order,
        item=instance.item,
        event=instance,
        notification_type=notification_type_for_event(instance),
        title=instance.title,
        message=instance.message,
        visible_to_customer=True,
        is_read_by_customer=False,
    )


@receiver(post_save, sender=OrderSupportMessage)
def create_notification_for_operator_support_reply(sender, instance, created, **kwargs):
    if not created:
        return

    if not instance.visible_to_customer:
        return

    if instance.sender_type != OrderSupportMessage.SENDER_OPERATOR:
        return

    if OrderCustomerNotification.objects.filter(support_message=instance).exists():
        return

    OrderCustomerNotification.objects.create(
        order=instance.order,
        item=instance.item,
        support_message=instance,
        notification_type=OrderCustomerNotification.TYPE_SUPPORT_REPLY,
        title="ოპერატორის ახალი პასუხი",
        message=instance.message,
        visible_to_customer=True,
        is_read_by_customer=False,
    )
