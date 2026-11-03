from shared import normalize


def classify(value):
    return "billing" if "invoice" in normalize(value) else "general"
