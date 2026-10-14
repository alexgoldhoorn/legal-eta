import legaleta


def test_disclaimer_present() -> None:
    assert legaleta.DISCLAIMER == "Planning aid, not legal advice."
