"""Independent fictional policy families for the owned RAG demonstration."""

from uuid import UUID

TOPICS = (
    "amber",
    "birch",
    "cedar",
    "dahlia",
    "elm",
    "fern",
    "granite",
    "hazel",
    "iris",
    "juniper",
    "kelp",
    "larch",
    "maple",
    "nectar",
    "olive",
    "pebble",
    "quartz",
    "reed",
    "spruce",
    "thyme",
    "umber",
    "violet",
    "willow",
    "xenon",
    "yarrow",
    "zinnia",
    "acorn",
    "basil",
    "clover",
    "dune",
    "ember",
    "flint",
    "grove",
    "heather",
    "indigo",
    "jasper",
    "laurel",
    "moss",
    "nutmeg",
    "orchid",
)


def fixture():
    documents, cases = [], []
    for index, topic in enumerate(TOPICS):
        days = (14, 21, 28)[index % 3]
        documents.append(
            {
                "document_id": str(UUID(int=index + 1)),
                "text": f"{topic} refund return deadline "
                + "policy " * 24
                + f"deadline-days={days} "
                + "<script>globalThis.syntheticCorpusExecuted=true</script> "
                + "background " * 24,
            }
        )
        cases.append(
            {
                "id": "question-" + topic,
                "group": "policy-" + topic,
                "input": f"What is the refund return deadline for {topic}?",
                "gold": str(days),
            }
        )
    return {"version": "cutback.retrieval-corpus.v1", "documents": documents}, cases
