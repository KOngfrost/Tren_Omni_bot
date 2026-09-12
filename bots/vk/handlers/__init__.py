"""Domain-specific handlers package for VK bot."""

from bots.vk.handlers.admin import admin_labeler
from bots.vk.handlers.events import events_labeler
from bots.vk.handlers.faq import faq_labeler
from bots.vk.handlers.knowledge import knowledge_labeler
from bots.vk.handlers.reports import reports_labeler
from bots.vk.handlers.student import student_labeler

__all__ = [
    "admin_labeler",
    "events_labeler",
    "faq_labeler",
    "knowledge_labeler",
    "reports_labeler",
    "student_labeler",
]
