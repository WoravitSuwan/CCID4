"""Open every page of the system as each role and check nothing crashes (no error 500).

A data set with one of everything (cases, calls with an attachment, summonses with EMS, an export, import
batches, signers, templates, users) is built once per test, then every URL in config/urls.py is opened with
real ids. Empty form posts check that bad or missing input gives a message, not a crash.
"""
import re

import pytest
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver

from apps.accounts.models import Role, User

pytestmark = pytest.mark.django_db

SKIP = {"logout", "healthz"}  # logout is checked on its own, last
PUBLIC = {"login", "branding_logo"}


def all_patterns(patterns=None, prefix=""):
    for p in patterns if patterns is not None else get_resolver().url_patterns:
        if isinstance(p, URLResolver):
            if str(p.pattern).startswith("django-admin"):
                continue  # Django's own admin site
            yield from all_patterns(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern), p.name or str(p.pattern)


@pytest.fixture
def world(imported, setup_signers, admin_user):
    from apps.calls.models import CallAttachment, CallOutcome
    from apps.calls.services import ensure_call_pool, record_call
    from apps.documents.models import DocTemplate, ExportFormat, ExportJob, ExportMode
    from apps.documents.services import issue, plan_issue, start_export
    from apps.imports.models import ImportKind
    from apps.registry.models import Case, InvestigatorAlias, Status, Unit
    from tests.conftest import SAMPLE, make_batch
    from tests.test_summons import issuable_cases, opts

    caller = User.objects.create_user("smoke_call", password="x" * 12, role=Role.OUTSOURCE)
    ensure_call_pool()
    summonses = issue(admin_user, plan_issue(admin_user, issuable_cases()[:2], opts()), opts())
    summonses[0].ems_number = "EB123456785TH"
    summonses[0].save()
    case = summonses[0].case
    attempt = record_call(case, caller, outcome=CallOutcome.OFF, note="ทดสอบ")
    attachment = CallAttachment.objects.create(attempt=attempt, case=case, original_name="slip.png", size=4,
                                               uploaded_by=caller)
    attachment.file.save("slip.png", ContentFile(b"\x89PNG"), save=True)
    job = ExportJob.objects.create(format=ExportFormat.DOCX, mode=ExportMode.values[0], include_envelope=True,
                                   created_by=admin_user)
    job.summonses.set(summonses)
    start_export(job)
    pending_batch = make_batch(ImportKind.REGISTER, SAMPLE / "mock_case_register.xlsx", user=admin_user)
    return {
        "case": case.pk, "other_case": Case.objects.exclude(pk=case.pk).first().pk,
        "summons": summonses[0].pk, "job": job.pk, "attempt": attempt.pk, "attachment": attachment.pk,
        "batch": pending_batch.pk, "done_batch": imported["register"].pk,
        "investigator": next(iter(setup_signers.values())).pk, "alias": InvestigatorAlias.objects.first().pk,
        "unit": Unit.objects.first().pk, "status": Status.objects.first().pk,
        "template": DocTemplate.objects.first().pk, "user": caller.pk, "caller": caller,
    }


def fill(path: str, name: str, w: dict) -> str:
    """Put a real id into each <int:...> part of the pattern."""
    group = name.split("_")[0]
    ids = {"case": w["case"], "call": w["case"], "summons": w["summons"], "export": w["job"],
           "import": w["batch"]}
    if name == "call_attachment" or name == "call_attachment_delete":
        pk = w["attachment"]
    elif name.startswith("settings_investigator"):
        pk = w["investigator"]
    elif name.startswith("settings_alias"):
        pk = w["alias"]
    elif name.startswith("settings_unit"):
        pk = w["unit"]
    elif name.startswith("settings_status"):
        pk = w["status"]
    elif name.startswith("settings_template"):
        pk = w["template"]
    elif name.startswith("settings_user"):
        pk = w["user"]
    else:
        pk = ids.get(group, w["case"])
    path = path.replace("<int:pk>", str(pk)).replace("<int:attempt_pk>", str(w["attempt"]))
    path = path.replace("<int:idx>", "0")
    assert "<" not in path, path
    return "/" + path


def crawl(client: Client, world: dict, method="get") -> list:
    problems = []
    for pattern, name in all_patterns():
        if name in SKIP:
            continue
        url = fill(pattern, name, world)
        resp = client.post(url, {}) if method == "post" else client.get(url)
        if resp.status_code >= 500:
            problems.append(f"{method.upper()} {url} ({name}) -> {resp.status_code}")
    return problems


def test_every_page_opens_for_admin(world, admin_user):
    client = Client(raise_request_exception=False)
    client.force_login(admin_user)
    assert crawl(client, world) == []


def test_every_page_opens_or_is_refused_for_outsource(world):
    client = Client(raise_request_exception=False)
    client.force_login(world["caller"])
    assert crawl(client, world) == []
    # admin-only pages are not shown to call staff
    for url in ("/settings/", "/settings/users/", "/settings/wipe/", "/cases/", "/imports/", "/settings/audit/"):
        assert client.get(url).status_code in (302, 403), url


def test_every_page_needs_login(world):
    client = Client(raise_request_exception=False)
    for pattern, name in all_patterns():
        if name in SKIP or name in PUBLIC:
            continue
        resp = client.get(fill(pattern, name, world))
        assert resp.status_code in (302, 403, 405), (name, resp.status_code)
        if resp.status_code == 302:
            assert resp["Location"].startswith("/accounts/login/"), (name, resp["Location"])
    assert client.get("/healthz").status_code == 200


def test_empty_form_posts_never_crash(world, admin_user):
    for user in (admin_user, world["caller"]):
        client = Client(raise_request_exception=False)
        client.force_login(user)
        assert crawl(client, world, method="post") == [], user.username


def test_bad_ids_give_not_found_not_crash(world, admin_user):
    client = Client(raise_request_exception=False)
    client.force_login(admin_user)
    bad = []
    for pattern, name in all_patterns():
        if name in SKIP or "<int:" not in pattern:
            continue
        url = "/" + re.sub(r"<int:\w+>", "999999", pattern)
        status = client.get(url).status_code
        if status >= 500:
            bad.append(f"{url} -> {status}")
    assert bad == []


def test_logout_needs_post(admin_user):
    client = Client()
    client.force_login(admin_user)
    assert client.post("/accounts/logout/").status_code in (200, 302)
    assert client.get("/calls/").status_code == 302  # logged out
