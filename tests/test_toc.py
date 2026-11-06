from __future__ import annotations

import pytest

from wmprevival.toc import Toc, TocError

JOY = "C+96+5370+9D42+EDBD+137FC+16546+1C6D5+2273F+29927+2EB78+32D76+36C84+3AC1A"
CD14 = "E+96+1FA9+5584+902E+C94F+FCC6+12C48+16909+1A227+1D472+206E5+23140+26868+294B9+2D186"
CD21 = (
    "15+96+3AC4+8111+BAEF+F10C+12F5C+181FC+1B036+1F8B9+22FE3+2662D+299A8+2D446+30978+35545"
    "+3844F+3BCAF+3E2C9+42F56+466AA+49694+4CFA3"
)


@pytest.mark.parametrize(
    ("wmp", "disc_id"),
    [
        # Calcolati anche dai Track*.cda dei dischi; quello da 21 tracce esiste su
        # MusicBrainz (CD stub), il che conferma l'algoritmo contro il server.
        (JOY, "TJb.4XmLklobrhFK9kddqt3kbc8-"),
        (CD14, "vmDt7K4IGb_crskc3Ul1.rHoKRE-"),
        (CD21, "VmZDxBuy2zdDckDHiiPB0XwTnNA-"),
    ],
)
def test_disc_id_of_real_cds(wmp: str, disc_id: str) -> None:
    assert Toc.from_wmp(wmp).disc_id == disc_id


def test_parse_and_round_trip() -> None:
    toc = Toc.from_wmp(JOY)
    assert toc.track_count == 12 and toc.first_track == 1 and toc.last_track == 12
    assert toc.offsets[0] == 150 and toc.leadout == 240666
    assert toc.to_wmp() == JOY


def test_mb_toc_parameter() -> None:
    assert Toc.from_wmp(JOY).mb_toc == (
        "1+12+240666+150+21360+40258+60861+79868+91462+116437+141119+170279+191352+208246+224388"
    )


def test_track_lengths_from_the_cda_files() -> None:
    # Traccia 1 del CD da 14 tracce: 7955 settori nel Track01.cda = 106.07 s.
    lengths = Toc.from_wmp(CD14).track_lengths_ms
    assert len(lengths) == 14 and lengths[0] == 106067


@pytest.mark.parametrize(
    "bad",
    ["", "C", "2+96", "3+96+200+300", "2+300+96+400", "2+96+200+150", "G+96+200", "0+96", "64+96+200"],
)
def test_invalid_tocs_are_rejected(bad: str) -> None:
    with pytest.raises(TocError):
        Toc.from_wmp(bad)
