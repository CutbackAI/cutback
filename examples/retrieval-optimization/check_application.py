"""A local retrieval regression check. This does not evaluate real model answers."""

from app import build_input


def main():
    frame = build_input("refund return deadline for amber")
    assert "deadline-days=14" in frame, "Retrieval lost the independent synthetic answer fact"
    assert len(frame.encode()) < 16000


if __name__ == "__main__":
    main()
