"""Owned synthetic policies with distinct policy identities and declared answer facts."""

import json


def fixture():
    words = (
        "amber",
        "birch",
        "cedar",
        "dawn",
        "elm",
        "fern",
        "grove",
        "hazel",
        "iris",
        "juniper",
        "kelp",
        "larch",
        "maple",
        "north",
        "oak",
        "pine",
        "quartz",
        "reed",
        "spruce",
        "thyme",
        "umber",
        "violet",
        "willow",
        "yarrow",
    )
    return [
        {
            "id": "policy-" + word,
            "input": json.dumps(
                {
                    "query": "refund deadline plan " + word,
                    "context": "refund deadline background " * 4
                    + "\n"
                    + f"{word} deadline-days={14 + index} "
                    + "detail " * 40
                    + "\n"
                    + "background notes\n" * 15,
                }
            ),
            "gold": str(14 + index),
        }
        for index, word in enumerate(words)
    ]
