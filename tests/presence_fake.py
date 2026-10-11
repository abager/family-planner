"""En simuleret Aula med Komme/Gå til testene. Svarene bygges med aula-pakkens egne modeller (from_dict), så
presence.py læser dem præcis som fra den rigtige pakke. Opfundne navne og id'er – aldrig rigtige data."""
from __future__ import annotations

import datetime as dt

from aula.models.child import Child
from aula.models.daily_overview import DailyOverview
from aula.models.pickup_responsible import ChildPickupResponsibles
from aula.models.presence import PresenceState
from aula.models.presence_registration import PresenceConfiguration
from aula.models.presence_template import PresenceWeekTemplate
from aula.models.profile import Profile
from aula.models.vacation_registration import VacationRegistration

HUGO, CARLA, LEO = 101, 102, 103


class HTTPish(Exception):
    """Som Aulas HttpRequestError: et barn uden Komme/Gå giver 400."""


class FakePresenceAula:
    def __init__(self, today: dt.date, with_presence=(HUGO, CARLA)):
        self.today = today
        self.with_presence = set(with_presence)
        self.status = {c: 3 for c in self.with_presence}            # 3 = til stede
        self.days: dict[tuple[int, str], dict] = {}
        for c in self.with_presence:
            for i in range(10):
                d = (today + dt.timedelta(days=i)).isoformat()
                self.days[(c, d)] = {"id": c * 1000 + i, "byDate": d, "repeatPattern": "never", "activityType": 0,
                                     "entryTime": "08:00", "exitTime": "15:30", "exitWith": "Mor Test (Mor)", "comment": ""}
        self.permissions = {c: {"report_sick": "editable", "pickup_times": "editable", "pickup_type": "editable"}
                            for c in self.with_presence}
        self.calls: list[tuple] = []
        self.self_decider = False                                    # tilbyder institutionen "Går selv"?
        self.fail_write: Exception | None = None
        self.ignore_writes = False                                   # Aula svarer ok, men gemmer intet
        self.opened = 0

    # --- som en åben klient (`async with await open_aula_client(cfg) as client`)
    async def __aenter__(self):
        self.opened += 1
        return self

    async def __aexit__(self, *a):
        return False

    async def get_profile(self):
        kids = [Child.from_dict({"id": cid, "profileId": cid + 900, "name": name})
                for cid, name in ((HUGO, "Hugo Test"), (CARLA, "Carla Test"), (LEO, "Leo Test"))]
        return Profile(profile_id=1, display_name="Far Test", children=kids, institution_profile_ids=[HUGO, CARLA, LEO, 1])

    async def get_calendar_events(self, ids, start, end):
        return []

    def _need(self, cid):
        if cid not in self.with_presence:
            raise HTTPish("400 Bad Request")

    async def get_daily_overview(self, child_id):
        self.calls.append(("overview", child_id))
        if child_id not in self.with_presence:
            return None
        d = self.days.get((child_id, self.today.isoformat()), {})
        return DailyOverview.from_dict({"id": 1, "status": self.status[child_id], "checkInTime": "08:02" if self.status[child_id] == 3 else None,
                                        "entryTime": d.get("entryTime"), "exitTime": d.get("exitTime"), "exitWith": d.get("exitWith")})

    async def get_presence_templates(self, ids, from_date, to_date):
        self.calls.append(("templates", tuple(ids), from_date.isoformat(), to_date.isoformat()))
        out = []
        for cid in ids:
            self._need(cid)
            days = [dict(v) for (c, d), v in sorted(self.days.items()) if c == cid and from_date.isoformat() <= d <= to_date.isoformat()]
            out.append(PresenceWeekTemplate.from_dict({"institutionProfile": {"id": cid}, "dayTemplates": days}))
        return out

    async def get_presence_configuration(self, ids):
        out = []
        for cid in ids:
            if cid not in self.with_presence:
                continue
            mods = [{"moduleType": m, "permission": p} for m, p in self.permissions[cid].items()]
            out.append(PresenceConfiguration.from_dict({"uniStudentId": cid, "presenceConfiguration": {
                "institution": {"name": "SFO Testhuset"}, "pickup": True, "goHomeWith": True, "selfDecider": self.self_decider,
                "dashboardModuleSettings": [{"presenceDashboardContext": "guardian_dashboard", "presenceModules": mods}]}}))
        return out

    async def get_pickup_responsibles(self, ids):
        return [ChildPickupResponsibles.from_dict({"uniStudentId": cid, "relatedPersons": [
            {"name": "Mor Test", "relation": "Mor"}, {"name": "Far Test", "relation": "Far"}],
            "pickupSuggestions": [{"id": 7, "pickUpName": "Mormor"}]}) for cid in ids if cid in self.with_presence]

    async def get_vacation_registrations(self, ids):
        return [VacationRegistration.from_dict({"vacationRegistrationId": 5, "title": "Juleferie", "startDate": "2026-12-21",
                                                "endDate": "2027-01-01", "isMissingAnswer": True}, {"id": HUGO})] if HUGO in ids else []

    # --- skrivning
    async def update_presence_status(self, ids, status):
        self.calls.append(("status", tuple(ids), getattr(status, "value", status)))
        if self.fail_write:
            raise self.fail_write
        if not self.ignore_writes:
            for cid in ids:
                self._need(cid)
                self.status[cid] = getattr(status, "value", status)
        return True

    async def update_presence_template(self, institution_profile_id, by_date, *, entry_time, exit_time, activity_type=0,
                                       exit_with=None, comment=None, template_id=None, repeat_pattern="Never", expires_at=None):
        act = getattr(activity_type, "value", activity_type)
        self.calls.append(("template", institution_profile_id, by_date.isoformat(), entry_time, exit_time, act, exit_with,
                           comment, template_id, repeat_pattern))
        if self.fail_write:
            raise self.fail_write
        if self.ignore_writes:
            return True
        self._need(institution_profile_id)
        rp = {"Never": "never", "Weekly": "weekly", "Every2Weeks": "every_2_weeks"}[repeat_pattern]
        step = {"never": None, "weekly": 7, "every_2_weeks": 14}[rp]
        d = by_date
        while True:
            key = (institution_profile_id, d.isoformat())
            old = self.days.get(key, {})
            self.days[key] = {"id": old.get("id", template_id), "byDate": d.isoformat(), "repeatPattern": rp,
                              "repeatFromDate": by_date.isoformat() if step else None, "activityType": act,
                              "entryTime": entry_time, "exitTime": exit_time, "exitWith": exit_with, "comment": comment or ""}
            if act == 1:          # som Aula (og pakken, der sender exitStartTime = exitEndTime): tidsrum, ingen exitTime
                self.days[key].update(exitTime=None, selfDeciderStartTime=exit_time, selfDeciderEndTime=exit_time)
            if not step:
                break
            d += dt.timedelta(days=step)
            if d > self.today + dt.timedelta(days=60):
                break
        return True


SICK = PresenceState.SICK.value
