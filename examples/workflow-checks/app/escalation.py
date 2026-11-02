from shared import normalize


def priority(value):
    return "urgent" if "blocked" in normalize(value) else "normal"
