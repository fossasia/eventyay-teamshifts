from django.contrib import admin

from .models import (
    CallForTeamMembers,
    Shift,
    ShiftAssignment,
    TeamApplicationAnswer,
    TeamApplicationQuestion,
    TeamMemberApplication,
    TeamRole,
)


@admin.register(CallForTeamMembers)
class CallForTeamMembersAdmin(admin.ModelAdmin):
    list_display = ("event", "title", "active", "deadline")
    list_filter = ("active",)
    search_fields = ("event__name", "event__slug")

    def get_readonly_fields(self, request, obj=None):
        # Allow selecting the event when creating; lock it on edits.
        if obj is not None:
            return ("event",)
        return ()


@admin.register(TeamRole)
class TeamRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "event")
    list_filter = ("event__organizer",)
    search_fields = ("name", "event__name", "event__slug")


@admin.register(TeamMemberApplication)
class TeamMemberApplicationAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "status", "created_at", "updated_at")
    list_filter = ("status", "role__event__organizer")
    search_fields = ("user__email", "role__name", "role__event__slug")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "event", "location", "start_time", "end_time", "capacity")
    list_filter = ("role__event__organizer",)
    search_fields = ("name", "role__name", "event__slug", "location")


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ("team_member", "shift", "is_moderator", "notified", "assigned_at")
    list_filter = ("is_moderator", "notified")
    search_fields = ("team_member__email", "shift__name")
    readonly_fields = ("assigned_at", "assigned_by", "shift", "team_member")

    def has_add_permission(self, request):
        # Assignments must go through plugin views to enforce capacity limits
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TeamApplicationQuestion)
class TeamApplicationQuestionAdmin(admin.ModelAdmin):
    list_display = ("event", "role", "variant", "required", "position", "active")
    list_filter = ("variant", "required", "active", "event__organizer")
    search_fields = ("event__slug", "role__name")
    ordering = ("event", "role", "position")


@admin.register(TeamApplicationAnswer)
class TeamApplicationAnswerAdmin(admin.ModelAdmin):
    list_display = ("application", "question")
    search_fields = (
        "application__user__email",
        "question__event__slug",
    )
    readonly_fields = ("application", "question")
