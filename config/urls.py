from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from django.urls import path

from apps.calls import views as cv
from apps.core import settings_views as sv
from apps.documents import views as dv
from apps.imports import views as iv
from apps.registry import views as rv

urlpatterns = [
    path("healthz", lambda r: HttpResponse("ok")),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="accounts/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/password/", auth_views.PasswordChangeView.as_view(
        template_name="accounts/password_change.html", success_url="/"), name="password_change"),
    path("", rv.dashboard, name="dashboard"),
    # register
    path("cases/", rv.case_list, name="case_list"),
    path("cases/export.xlsx", rv.export_register, name="case_export"),
    path("cases/postcode/", rv.postcode_lookup, name="postcode_lookup"),
    path("cases/<int:pk>/", rv.case_detail, name="case_detail"),
    path("cases/<int:pk>/address/", rv.case_address_save, name="case_address_save"),
    path("cases/<int:pk>/address/resolve/", rv.case_address_resolve, name="case_address_resolve"),
    path("cases/<int:pk>/reveal-id/", rv.reveal_id, name="case_reveal_id"),
    path("cases/<int:pk>/issue/", dv.issue_from_case, name="case_issue"),
    path("cases/<int:pk>/status/", rv.case_status_update, name="case_status_update"),
    path("cases/status/bulk/", rv.case_status_bulk, name="case_status_bulk"),
    # imports
    path("imports/", iv.import_home, name="import_home"),
    path("imports/<int:pk>/sheets/", iv.import_sheets, name="import_sheets"),
    path("imports/<int:pk>/mapping/", iv.import_mapping, name="import_mapping"),
    path("imports/<int:pk>/preview/", iv.import_preview, name="import_preview"),
    path("imports/<int:pk>/confirm/", iv.import_confirm, name="import_confirm"),
    path("imports/<int:pk>/cancel/", iv.import_cancel, name="import_cancel"),
    path("imports/<int:pk>/issues.xlsx", iv.import_issues_download, name="import_issues"),
    # summons
    path("summons/", dv.summons_list, name="summons_list"),
    path("summons/issue/start/", dv.issue_start, name="issue_start"),
    path("summons/issue/", dv.issue_form, name="issue_form"),
    path("summons/issue/signer/", dv.issue_signer, name="issue_signer"),
    path("summons/<int:pk>/result/", rv.summons_result, name="summons_result"),
    path("summons/<int:pk>/docx/", dv.summons_docx, name="summons_docx"),
    path("summons/<int:pk>/pdf/", dv.summons_pdf, name="summons_pdf"),
    path("exports/", dv.export_jobs, name="export_jobs"),
    path("exports/new/", dv.export_new, name="export_new"),
    path("exports/<int:pk>/", dv.export_job, name="export_job"),
    path("exports/<int:pk>/download/", dv.export_download, name="export_download"),
    path("exports/<int:pk>/retry-word/", dv.export_retry_word, name="export_retry_word"),
    # victim phone follow-up (outsource call staff)
    path("calls/", cv.queue, name="calls_queue"),
    path("calls/assign/", cv.assign_view, name="calls_assign"),
    path("calls/assign/bulk/", cv.assign_bulk, name="calls_assign_bulk"),
    path("calls/export.xlsx", cv.export_calls, name="calls_export"),
    path("calls/<int:pk>/", cv.call_case, name="call_case"),
    path("calls/<int:pk>/reopen/", cv.reopen, name="call_reopen"),
    path("calls/<int:pk>/address/", cv.call_address_save, name="call_address_save"),
    # settings
    path("settings/", sv.settings_home, name="settings_home"),
    path("settings/investigators/", sv.investigators, name="settings_investigators"),
    path("settings/investigators/new/", sv.investigator_edit, name="settings_investigator_new"),
    path("settings/investigators/import/", sv.signer_import, name="settings_signer_import"),
    path("settings/investigators/template.xlsx", sv.signer_template, name="settings_signer_template"),
    path("settings/investigators/<int:pk>/", sv.investigator_edit, name="settings_investigator_edit"),
    path("settings/aliases/<int:pk>/link/", sv.alias_link, name="settings_alias_link"),
    path("settings/aliases/<int:pk>/unlink/", sv.alias_unlink, name="settings_alias_unlink"),
    path("settings/units/", sv.units, name="settings_units"),
    path("settings/units/new/", sv.unit_edit, name="settings_unit_new"),
    path("settings/units/<int:pk>/", sv.unit_edit, name="settings_unit_edit"),
    path("settings/statuses/", sv.statuses, name="settings_statuses"),
    path("settings/statuses/<int:pk>/", sv.status_toggle, name="settings_status_toggle"),
    path("settings/templates/", sv.templates, name="settings_templates"),
    path("settings/templates/<int:pk>/", sv.template_action, name="settings_template_action"),
    path("settings/templates/<int:pk>/download/", sv.template_download, name="settings_template_download"),
    path("settings/users/", sv.users, name="settings_users"),
    path("settings/users/new/", sv.user_edit, name="settings_user_new"),
    path("settings/users/<int:pk>/", sv.user_edit, name="settings_user_edit"),
    path("settings/system/", sv.system, name="settings_system"),
    path("settings/audit/", sv.audit, name="settings_audit"),
    path("django-admin/", admin.site.urls),
]
