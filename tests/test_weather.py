"""weather.py mod et simuleret MET Norway (httpx.MockTransport). Ingen rigtig netværkstrafik."""
from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

import weather as W

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 1, 6, 0, tzinfo=W.TZ)                    # torsdag kl. 6 dansk tid
HOME = (55.6761, 12.5683)


def met(start=dt.datetime(2026, 10, 1, 3, 0, tzinfo=UTC), hours=60, temp=lambda h: 10, rain=lambda h: 0.0,
        wind=lambda h: 4, gust=lambda h: 7, cloud=lambda h: 0.5, sym=None, six_hourly_after=None):
    """GeoJSON som MET's Locationforecast 2.0 /complete. `rain(h)` er mm i timen efter h (lokal tid);
    `cloud(h)` er 0–1 (MET sender procent). `six_hourly_after`: derefter kun 6-timers-blokke (som MET)."""
    series = []
    for i in range(hours):
        t = start + dt.timedelta(hours=i)
        lh = t.astimezone(W.TZ)
        data = {"instant": {"details": {"air_temperature": temp(lh), "wind_speed": wind(lh),
                                        "wind_speed_of_gust": gust(lh), "cloud_area_fraction": cloud(lh) * 100}}}
        if six_hourly_after is None or i < six_hourly_after:
            nxt = {"details": {"precipitation_amount": rain(lh)}}
            if sym:
                nxt["summary"] = {"symbol_code": sym(lh)}
            data["next_1_hours"] = nxt
        data["next_6_hours"] = {"details": {"precipitation_amount": 0.0}}
        series.append({"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": data})
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [12.57, 55.68, 10]},
            "properties": {"meta": {"updated_at": "2026-10-01T03:00:00Z"}, "timeseries": series}}


class Fake:
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, req):
        self.requests.append(req)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, httpx.Response):                              # færdigt svar med headere (Expires mv.)
            return httpx.Response(reply.status_code, headers=reply.headers, content=reply.content)
        status, body = reply
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, content=json.dumps(body) if not isinstance(body, str) else body)


def home(tmp_path, at=HOME):
    W.save_home(tmp_path, *at, NOW)
    return tmp_path


def T(fake):
    return httpx.MockTransport(fake)


# ---------------------------------------------------------------- hjemmets placering
def test_home_is_rounded_to_about_one_km_before_it_is_saved(tmp_path):
    h = W.save_home(tmp_path, 55.676098, 12.568337, NOW)
    assert (h["lat"], h["lon"]) == (55.68, 12.57)
    raw = (tmp_path / W.HOME_FILE).read_text("utf-8")
    assert "55.676" not in raw and "12.568" not in raw


@pytest.mark.parametrize("lat,lon", [(48.85, 2.35), (64.1, -21.9), (0, 0), ("x", 1), (None, None)])
def test_a_home_outside_denmark_is_refused(tmp_path, lat, lon):
    with pytest.raises(ValueError):
        W.save_home(tmp_path, lat, lon, NOW)
    assert W.load_home(tmp_path) is None


def test_bornholm_and_skagen_are_in_denmark():
    assert W.valid_location(55.10, 14.90) and W.valid_location(57.72, 10.58)


def test_no_home_means_no_weather_and_no_request(tmp_path):
    fake = Fake((200, met()))
    assert W.hours(tmp_path, NOW, T(fake)) is None and not fake.requests


# ---------------------------------------------------------------- hentning og fair brug
def test_only_the_rounded_home_is_sent_to_met(tmp_path):
    fake = Fake((200, met()))
    W.hours(home(tmp_path), NOW, T(fake))
    req = fake.requests[0]
    assert req.url.host == "api.met.no" and req.url.path == "/weatherapi/locationforecast/2.0/complete"
    assert dict(req.url.params) == {"lat": "55.68", "lon": "12.57"}           # højst 2 decimaler
    assert "authorization" not in req.headers and "key" not in str(req.url)


def test_met_is_asked_at_most_once_an_hour(tmp_path):
    fake = Fake((200, met()))
    d = home(tmp_path)
    for minutes in (0, 15, 30, 45):
        assert W.hours(d, NOW + dt.timedelta(minutes=minutes), T(fake))
    assert len(fake.requests) == 1
    W.hours(d, NOW + dt.timedelta(minutes=61), T(fake))
    assert len(fake.requests) == 2


def test_met_down_reuses_a_recent_forecast_and_waits_before_asking_again(tmp_path):
    fake = Fake((200, met()), (503, "nede"))
    d = home(tmp_path)
    W.hours(d, NOW, T(fake))
    later = NOW + dt.timedelta(hours=2)
    assert W.hours(d, later, T(fake))                                   # gammel prognose bruges
    assert W.hours(d, later + dt.timedelta(minutes=15), T(fake))        # ingen ny forespørgsel under ventetiden
    assert len(fake.requests) == 2


def test_met_down_for_long_gives_no_weather_rather_than_old_weather(tmp_path):
    fake = Fake((200, met()), (0, httpx.ConnectError("x")))
    d = home(tmp_path)
    W.hours(d, NOW, T(fake))
    assert W.hours(d, NOW + dt.timedelta(hours=7), T(fake)) is None


@pytest.mark.parametrize("reply", [(500, "fejl"), (200, "ikke json"), (200, {"features": []}), (0, httpx.ReadTimeout("t"))],
                         ids=["500", "ikke-json", "tomt", "timeout"])
def test_any_met_problem_gives_no_weather_and_never_raises(tmp_path, reply):
    assert W.hours(home(tmp_path), NOW, T(Fake(reply))) is None


def test_a_new_home_fetches_again(tmp_path):
    fake = Fake((200, met()))
    d = home(tmp_path)
    W.hours(d, NOW, T(fake))
    W.save_home(d, 56.15, 10.21, NOW)                                   # flyttet til Aarhus
    W.hours(d, NOW + dt.timedelta(minutes=5), T(fake))
    assert len(fake.requests) == 2 and dict(fake.requests[1].url.params) == {"lat": "56.15", "lon": "10.21"}


# ---------------------------------------------------------------- tolkning af MET Norways tal
def test_met_units_become_rows_with_rain_per_hour_and_cloud_as_a_fraction():
    rows = W.parse(met(hours=5, temp=lambda h: 12.4, rain=lambda h: 1.5, cloud=lambda h: 0.25, gust=lambda h: 11))
    r = rows[0]
    assert r["temp"] == 12.4 and r["rain"] == 1.5 and r["cloud"] == 0.25 and r["gust"] == 11
    assert [x["t"].hour for x in rows] == [3, 4, 5, 6, 7]


def test_only_hourly_blocks_are_used_not_mets_six_hour_tail():
    rows = W.parse(met(hours=12, six_hourly_after=8))
    assert len(rows) == 8


# ---------------------------------------------------------------- dagsresumé
def day(rows_kw, now=NOW, which=0):
    return W.summarize(W.parse(met(**rows_kw)), now)[which]


def test_a_dry_mild_day():
    d = day({"temp": lambda h: 8 + (h.hour - 7) * 0.3})
    assert (d["dato"], d["ugedag"], d["min"], d["max"], d["regn"]) == ("2026-10-01", "torsdag", 8, 12, "ingen")
    assert d["raad"] == [] and d["tekst"] == "8–12°, skyet"


def test_rain_in_the_afternoon_means_rain_gear():
    d = day({"rain": lambda h: 1.5 if 14 <= h.hour <= 17 else 0.0})
    assert d["regn"] == "regn" and d["regn_hvornaar"] == "om eftermiddagen"
    assert "regntøj og gummistøvler" in d["raad"] and "regn om eftermiddagen" in d["tekst"]


def test_a_cold_frosty_morning_means_mittens():
    d = day({"temp": lambda h: -2 if h.hour < 9 else 4})
    assert d["frost"] and "vanter og hue" in d["raad"] and "frost om morgenen" in d["tekst"]


def test_strong_wind_and_hot_sun():
    windy = day({"wind": lambda h: 15})
    assert windy["vind"] == "hård" and any("vind" in r for r in windy["raad"])
    hot = day({"temp": lambda h: 26, "cloud": lambda h: 0.1})
    assert hot["himmel"] == "sol" and "solcreme og kasket" in hot["raad"] and "ekstra drikkevand" in hot["raad"]


def test_only_days_met_covers_hourly_are_included():
    days = W.summarize(W.parse(met(hours=60)), NOW)                     # fra torsdag kl. 5 til lørdag kl. 17
    assert [d["dato"] for d in days] == ["2026-10-01", "2026-10-02"]    # lørdag mangler aftenen → udeladt


def test_today_only_counts_the_hours_left():
    morning_rain = {"rain": lambda h: 3.0 if h.hour == 8 else 0.0}
    assert day(morning_rain)["regn"] == "regn"
    assert day(morning_rain, now=dt.datetime(2026, 10, 1, 11, 0, tzinfo=W.TZ))["regn"] == "ingen"
    late = W.summarize(W.parse(met()), dt.datetime(2026, 10, 1, 20, 30, tzinfo=W.TZ))
    assert late[0]["dato"] == "2026-10-02"                              # efter kl. 19 er i dag forbi


def test_small_forecast_changes_give_the_same_summary():
    a = day({"temp": lambda h: 9.8, "rain": lambda h: 0.1})
    b = day({"temp": lambda h: 10.2, "rain": lambda h: 0.12})
    assert a == b


# ---------------------------------------------------------------- til family.json
def test_family_json_gets_the_summary_but_never_the_location(cfg):
    from pathlib import Path
    out = Path(cfg["output"]).parent
    W.save_home(out, *HOME, NOW)
    w = W.for_family(cfg, NOW, T(Fake((200, met()))))
    assert w["kilde"] == "MET Norway" and len(w["dage"]) == 2 and w["dage"][0]["ikon"]
    txt = json.dumps(w)
    assert "55.6" not in txt and "12.5" not in txt and "lat" not in txt


def test_family_json_has_no_weather_without_a_home_or_when_turned_off(cfg):
    assert W.for_family(cfg, NOW, T(Fake((200, met())))) is None
    cfg["weather"] = {"enabled": False}
    from pathlib import Path
    W.save_home(Path(cfg["output"]).parent, *HOME, NOW)
    assert W.for_family(cfg, NOW, T(Fake((200, met())))) is None


def test_weather_state_files_are_owner_only(tmp_path):
    import os
    import stat
    W.hours(home(tmp_path), NOW, T(Fake((200, met()))))
    if os.name == "posix":
        for f in (W.HOME_FILE, W.CACHE_FILE):
            assert stat.S_IMODE(os.stat(tmp_path / f).st_mode) == 0o600


# ---------------------------------------------------------------- time for time (til visningen i appen)
def test_each_covered_day_gets_its_hours_from_6_to_22(cfg):
    from pathlib import Path
    W.save_home(Path(cfg["output"]).parent, *HOME, NOW)
    w = W.for_family(cfg, NOW, T(Fake((200, met(hours=72)))))
    first = w["dage"][0]["timer"]
    assert [h["kl"] for h in first] == list(range(6, 23))
    assert set(first[0]) == {"kl", "ikon", "temp", "regn", "vind"}
    assert "lat" not in json.dumps(w["dage"][0]["timer"])


def test_hours_show_rain_sun_cloud_and_snow():
    rows = W.parse(met(hours=30, temp=lambda h: -1 if h.hour == 9 else 10,
                       rain=lambda h: 1.0 if h.hour in (9, 14) else 0.0,
                       cloud=lambda h: 0.1 if h.hour == 12 else 0.5 if h.hour == 13 else 0.9))
    hs = {h["kl"]: h for h in W.hourly(rows, "2026-10-01", *HOME)}
    assert hs[9]["ikon"] == W.SNOW and hs[14]["ikon"] == W.RAIN and hs[14]["regn"] == 1.0
    assert hs[12]["ikon"] == W.SUN and hs[13]["ikon"] == W.SUN_CLOUD and hs[15]["ikon"] == W.CLOUD


def test_clear_night_hours_get_a_moon_not_a_sun():
    rows = W.parse(met(hours=30, cloud=lambda h: 0.1))
    hs = {h["kl"]: h["ikon"] for h in W.hourly(rows, "2026-10-01", *HOME)}
    assert hs[6] == W.MOON and hs[21] == W.MOON                       # oktober i København: mørkt kl. 6 og 21
    assert hs[12] == W.SUN


@pytest.mark.parametrize("when,up", [((2026, 6, 21, 4, 30), True), ((2026, 12, 21, 8, 0), False),
                                     ((2026, 12, 21, 12, 0), True), ((2026, 12, 21, 16, 30), False)])
def test_sun_up_follows_the_danish_seasons(when, up):
    assert W.sun_up(dt.datetime(*when, tzinfo=W.TZ), *HOME) is up


def test_the_hours_never_reach_the_ai(cfg):
    import briefing as B
    from pathlib import Path
    W.save_home(Path(cfg["output"]).parent, *HOME, NOW)
    w = W.for_family(cfg, NOW, T(Fake((200, met()))))
    assert w["dage"][0]["timer"]
    data = {"weather": w, "events": [], "tasks": [], "weekplan": [], "posts": [], "messages": [], "people": []}
    payload = B.build_digest(data, NOW.date(), NOW.date(), NOW)
    assert payload["vejr"] and all("timer" not in v for v in payload["vejr"])


# ---------------------------------------------------------------- logning: én linje pr. kørsel, aldrig koordinater
def test_the_log_says_when_home_is_not_set(cfg, caplog):
    caplog.set_level("INFO", logger="familieplanner.weather")
    W.for_family(cfg, NOW, T(Fake((200, met()))))
    assert "hjemmets placering er ikke sat" in caplog.text and "Vejr: hjem" in caplog.text


def test_the_log_says_how_many_days_and_when_fetched(cfg, caplog):
    from pathlib import Path
    W.save_home(Path(cfg["output"]).parent, *HOME, NOW)
    caplog.set_level("INFO", logger="familieplanner.weather")
    W.for_family(cfg, NOW, T(Fake((200, met()))))
    assert "Vejr: 2 dage fra MET Norway (prognose hentet kl. 06:00)" in caplog.text
    assert "55.6" not in caplog.text and "12.5" not in caplog.text


def test_the_log_says_when_met_gives_nothing(cfg, caplog):
    from pathlib import Path
    W.save_home(Path(cfg["output"]).parent, *HOME, NOW)
    caplog.set_level("INFO", logger="familieplanner.weather")
    assert W.for_family(cfg, NOW, T(Fake((503, "nede")))) is None
    assert "MET Norway svarede 503" in caplog.text and "overblikket er uden vejr" in caplog.text


def test_the_log_says_when_weather_is_turned_off(cfg, caplog):
    cfg["weather"] = {"enabled": False}
    caplog.set_level("INFO", logger="familieplanner.weather")
    W.for_family(cfg, NOW)
    assert "slået fra" in caplog.text


def test_status_is_only_yes_or_no(cfg, tmp_path):
    assert W.status(cfg, tmp_path) == {"enabled": True, "home": False}
    W.save_home(tmp_path, *HOME, NOW)
    assert W.status(cfg, tmp_path) == {"enabled": True, "home": True}
    cfg["weather"] = {"enabled": False}
    assert W.status(cfg, tmp_path)["enabled"] is False



# ---------------------------------------------------------------- forespørgslen til MET Norway og deres vilkår
def test_the_user_agent_identifies_the_app_with_the_repo_link(tmp_path):
    fake = Fake((200, met()))
    W.hours(home(tmp_path), NOW, T(fake))
    assert fake.requests[0].headers["user-agent"] == "Familieplan/1.0 https://github.com/abager/family-planner"


def test_the_contact_can_be_changed_in_config(tmp_path):
    fake = Fake((200, met()))
    W.hours(home(tmp_path), NOW, T(fake), cfg={"weather": {"contact": "andreas@example.com"}})
    assert fake.requests[0].headers["user-agent"] == "Familieplan/1.0 andreas@example.com"


def test_met_symbols_become_icons_with_night_versions():
    assert W.symbol_icon("clearsky_day") == W.SUN and W.symbol_icon("clearsky_night") == W.MOON
    assert W.symbol_icon("fair_day") == W.FAIR and W.symbol_icon("partlycloudy_day") == W.SUN_CLOUD
    assert W.symbol_icon("cloudy") == W.CLOUD and W.symbol_icon("fog") == W.FOG
    assert W.symbol_icon("lightrainshowers_day") == W.RAIN and W.symbol_icon("heavyrain") == W.RAIN
    assert W.symbol_icon("lightsleet") == W.SNOW and W.symbol_icon("snowshowers_polartwilight") == W.SNOW
    assert W.symbol_icon("rainandthunder") == W.THUNDER and W.symbol_icon("noget_nyt") is None


def test_the_hours_use_mets_symbol_when_it_is_there():
    rows = W.parse(met(hours=30, sym=lambda h: "rainshowers_day" if h.hour == 12 else "clearsky_night"))
    hs = {h["kl"]: h["ikon"] for h in W.hourly(rows, "2026-10-01", *HOME)}
    assert hs[12] == W.RAIN and hs[21] == W.MOON


def test_not_modified_keeps_the_cached_forecast_and_asks_politely(tmp_path):
    first = Fake(httpx.Response(200, json=met(), headers={"last-modified": "Thu, 01 Oct 2026 03:10:00 GMT",
                                                          "expires": "Thu, 01 Oct 2026 04:30:00 GMT"}))
    d = home(tmp_path)
    rows = W.hours(d, NOW, T(first))
    again = Fake(httpx.Response(304))
    later = W.hours(d, NOW + dt.timedelta(hours=2), T(again))
    assert again.requests[0].headers["if-modified-since"] == "Thu, 01 Oct 2026 03:10:00 GMT"
    assert [r["t"] for r in later] == [r["t"] for r in rows]


def test_nothing_is_asked_before_mets_expires(tmp_path):
    first = Fake(httpx.Response(200, json=met(), headers={"expires": "Thu, 01 Oct 2026 08:00:00 GMT"}))
    d = home(tmp_path)
    W.hours(d, NOW, T(first))
    again = Fake(httpx.Response(200, json=met()))
    W.hours(d, NOW + dt.timedelta(minutes=90), T(again))                 # 05.30 UTC < Expires 08.00 UTC
    assert again.requests == []


def test_an_old_dmi_cache_is_not_reused(tmp_path):
    d = home(tmp_path)
    (d / W.CACHE_FILE).write_text(json.dumps({"lat": 55.68, "lon": 12.57, "fetched": NOW.isoformat(),
                                              "hours": [{"t": NOW.isoformat(), "temp": 1}]}), "utf-8")
    fake = Fake((200, met()))
    W.hours(d, NOW + dt.timedelta(minutes=5), T(fake))
    assert len(fake.requests) == 1                                       # ingen "source": hentes på ny


def test_a_rejection_logs_mets_reason_without_coordinates():
    body = "<html><body>403 Forbidden: missing User-Agent for lat=55.676098 lon=12.568337</body></html>"
    with pytest.raises(W.WeatherUnavailable) as e:
        W.fetch(*HOME, transport=httpx.MockTransport(lambda req: httpx.Response(403, text=body)))
    msg = str(e.value)
    assert "MET Norway svarede 403" in msg and "missing User-Agent" in msg
    assert "55.6" not in msg and "12.5" not in msg
