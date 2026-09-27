"""Speaker predictions for runners out on course."""

from datetime import datetime

import speaker
from results import build_result, rank_results

D = datetime(2026, 5, 17)


def t(h, m):
    return D.replace(hour=h, minute=m)


def test_time_to_beat_radio_place_and_prediction():
    course = {"type": "linear", "controls": [31, 32]}
    runs = [
        {"id": 1, "name": "Leader", "class": "M", "start": t(10, 0),
         "punches": [(31, t(10, 20)), (32, t(10, 40))], "finish": t(11, 0)},
        {"id": 2, "name": "Out Fast", "class": "M", "start": t(10, 10),
         "punches": [(31, t(10, 25))], "finish": None},
        {"id": 3, "name": "Out No Radio", "class": "M", "start": t(10, 30),
         "punches": [], "finish": None},
    ]
    results = rank_results([build_result(r, course) for r in runs])["M"]
    rows = {r["name"]: r for r in speaker.out_on_course(
        [{"class": {"name": "M"}, "course": course, "results": results}])}
    assert set(rows) == {"Out Fast", "Out No Radio"}
    fast = rows["Out Fast"]
    assert fast["must_finish_by"] == "11:10:00"
    assert fast["split"] == "15:00" and fast["split_place"] == 1   # 15 min vs leader's 20
    # 60 min leader * 15/20 = 45 min -> would win.
    assert fast["predicted"] == "45:00" and fast["predicted_place"] == 1
    assert rows["Out No Radio"]["last_control"] is None
    assert rows["Out No Radio"]["must_finish_by"] == "11:30:00"


def test_speaker_page_renders():
    import app as appmod
    assert "To lead" in appmod.app.test_client().get("/speaker").get_data(as_text=True)
