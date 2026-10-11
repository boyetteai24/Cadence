import pytest

from cadence.cleanup.rules import RuleCleaner

clean = RuleCleaner(replacements={"claude code": "Claude Code"}).clean


@pytest.mark.parametrize(
    "raw, expected",
    [
        # fillers
        ("Um, so I think, uh, we should go.", "So I think, we should go."),
        ("um uh hmm", ""),
        ("It was, you know, fine.", "It was, fine."),
        ("The umbrella is here.", "The umbrella is here."),  # 'um' inside a word
        # repetitions and stutters
        ("I I think the the plan works.", "I think the plan works."),
        ("I want to, I want to go home.", "I want to go home."),
        ("I'm using I'm using it now.", "I'm using it now."),
        ("we're done, we're done here.", "We're done here."),
        ("I-I-I think so.", "I think so."),
        ("I w-want that.", "I want that."),
        ("He had had enough. Very very good.", "He had had enough. Very very good."),
        # corrections
        ("Let's meet at 3pm, no wait, 4pm.", "Let's meet at 4pm."),
        ("Send it to John, sorry, I meant Jane.", "Send it to Jane."),
        ("I'm using flow or sorry, I'm using Cadence.", "I'm using Cadence."),
        ("Call Tom, sorry I mean Tim.", "Call Tim."),
        ("I'm using I'm using flow, or sorry, Cadence right here.", "I'm using Cadence right here."),
        ("I'll go on Tuesday, no wait, on Wednesday.", "I'll go on Wednesday."),
        ("Buy milk. Scratch that. Buy eggs.", "Buy eggs."),
        ("This is wrong scratch that this is right.", "This is right."),
        # voice commands
        ("Hi John, new paragraph, thanks for the update.", "Hi John\n\nThanks for the update."),
        ("first item new line second item", "First item\nSecond item"),
        ("Meet at 4. New paragraph. Thanks.", "Meet at 4.\n\nThanks."),
        # tidy
        ("i think i'm ready, i.e. done.", "I think I'm ready, i.e. done."),
        ("hello there. how are you?", "Hello there. How are you?"),
        ("meet at 4. then we eat.", "Meet at 4. Then we eat."),
        ("ask J. smith, e.g. tomorrow.", "Ask J. smith, e.g. tomorrow."),
        # dictionary
        ("I use claude code daily.", "I use Claude Code daily."),
    ],
)
def test_rules(raw, expected):
    assert clean(raw) == expected


def test_restated_correction_replaces_from_the_repeated_word():
    assert clean("I need milk, no wait, I need to buy some fresh eggs today.") == (
        "I need to buy some fresh eggs today."
    )


@pytest.mark.parametrize("text", [
    "There's no wait, the line is empty today.",  # not a correction
    "Meet Bob, or sorry, Rob and Ann at the new place.",  # too long to align safely
])
def test_ambiguous_correction_left_alone(text):
    assert clean(text) == text


def test_fillers_can_be_disabled():
    assert RuleCleaner(remove_fillers=False).clean("um hello") == "Um hello"


def test_long_text_has_no_limit():
    text = "This is a sentence. " * 5000
    assert clean(text).count("This is a sentence.") == 5000
