from datetime import date, datetime, timezone

from app.services.football_data_uk_parser import (
    csv_url, current_season_start_year, parse_csv, season_code_from_year, season_label_from_code,
)
from app.services.team_names import best_match, canonical_key, normalize_team_name

SAMPLE = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HS,AS,HC,AC,B365H,B365D,B365A,PSCH,PSCD,PSCA,"
    "P>2.5,P<2.5,AHh,PAHH,PAHA,AHCh,PCAHH,PCAHA\n"
    "E0,16/08/2025,20:00,Man United,Arsenal,0,1,A,0,1,10,12,4,6,2.5,3.4,2.7,2.6,3.3,2.8,1.9,1.95,0.25,1.9,2.0,0.5,1.85,2.05\n"
    "E0,23/08/25,,Liverpool,Wolves,,,,,,,,,,1.5,4.2,6.0,,,,,,,,,,,\n"
    ",,,,,,,,,,,,,,,,,,,,,,,,,,,\n"
)


def test_season_helpers():
    assert season_code_from_year(2025) == "2526"
    assert season_label_from_code("2526") == "2025"
    assert season_label_from_code("9900") == "1999"
    assert current_season_start_year(date(2026, 11, 27)) == 2026
    assert current_season_start_year(date(2026, 3, 1)) == 2025
    assert csv_url("E0", "2526") == "https://www.football-data.co.uk/mmz4281/2526/E0.csv"


def test_parse_finished_and_scheduled_rows():
    rows = parse_csv(("\ufeff" + SAMPLE).encode("utf-8"))
    assert len(rows) == 2

    first = rows[0]
    assert first["home"] == "Man United" and first["status"] == "FINISHED"
    assert (first["home_score"], first["away_score"]) == (0, 1)
    assert first["kickoff"].tzinfo is not None
    assert first["kickoff"] == datetime(2025, 8, 16, 19, 0, tzinfo=timezone.utc)  # 20:00 em Londres (BST)
    assert first["statistics"]["home_shots"] == 10

    keys = {(o["bookmaker"], o["market"], o["selection"], o["kind"], o["line"]): o["price"] for o in first["odds"]}
    assert keys[("bet365", "1X2", "H", "pre_match", 0.0)] == 2.5
    assert keys[("pinnacle", "1X2", "A", "closing", 0.0)] == 2.8
    assert keys[("pinnacle", "OU25", "OVER", "pre_match", 2.5)] == 1.9
    assert keys[("pinnacle", "AH", "HOME", "pre_match", 0.25)] == 1.9
    assert keys[("pinnacle", "AH", "AWAY", "closing", 0.5)] == 2.05

    second = rows[1]
    assert second["status"] == "SCHEDULED" and second["home_score"] is None
    assert second["date"] == date(2025, 8, 23)  # data com ano de 2 dígitos


def test_parse_cp1252_names():
    content = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nT1,01/09/2025,Beşiktaş,Fenerbahçe,1,1\n".encode("cp1252", errors="replace")
    rows = parse_csv(content)
    assert len(rows) == 1


def test_team_name_matching():
    assert normalize_team_name("Nott'm Forest") == "nottm forest"
    assert normalize_team_name("1. FC Köln") == "koln"
    assert normalize_team_name("Brighton & Hove Albion FC") == "brighton and hove albion"

    index = {
        normalize_team_name(name): name
        for name in ["Manchester United FC", "Wolverhampton Wanderers FC", "Tottenham Hotspur FC",
                     "Newcastle United FC", "FC Bayern München", "Borussia Dortmund", "AC Milan",
                     "FC Internazionale Milano"]
    }
    assert best_match(canonical_key("Man United"), index) == "Manchester United FC"
    assert best_match(canonical_key("Wolves"), index) == "Wolverhampton Wanderers FC"
    assert best_match(canonical_key("Tottenham"), index) == "Tottenham Hotspur FC"
    assert best_match(canonical_key("Newcastle"), index) == "Newcastle United FC"
    assert best_match(canonical_key("Bayern Munich"), index) == "FC Bayern München"
    assert best_match(canonical_key("Dortmund"), index) == "Borussia Dortmund"
    assert best_match(canonical_key("Milan"), index) == "AC Milan"
    assert best_match(canonical_key("Inter"), index) == "FC Internazionale Milano"
    assert best_match(canonical_key("Equipa Inexistente"), index) is None
