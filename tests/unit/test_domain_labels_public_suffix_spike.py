"""SPIKE: names the hand-written two-part suffix list gets wrong and the Public Suffix List gets right."""

import pytest

from jittersim.config.domain_labels import registrable_domain


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("www.example.gov.in", "example.gov.in"),
        ("shop.example.ac.in", "example.ac.in"),
        ("www.foo.org.il", "foo.org.il"),
        ("x.gov.pl", "x.gov.pl"),
        ("www.example.nic.in", "example.nic.in"),
        ("www.y.org.br", "y.org.br"),
        ("www.x.co.uk", "x.co.uk"),
    ],
)
def test_registrable_domain_under_suffixes_the_hand_list_lacks(domain, expected):
    assert registrable_domain(domain) == expected
