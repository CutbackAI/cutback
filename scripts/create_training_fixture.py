"""Create an OWNED, randomly initialized tiny CPU model for adapter tests. No downloads."""

import argparse
from pathlib import Path


def create(root: Path):
    import torch
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast

    root.mkdir(parents=True, exist_ok=False)
    torch.manual_seed(17)
    model = GPT2LMHeadModel(
        GPT2Config(
            vocab_size=12,
            n_positions=64,
            n_embd=16,
            n_layer=1,
            n_head=2,
            bos_token_id=1,
            eos_token_id=2,
            pad_token_id=0,
        )
    )
    model.save_pretrained(root, safe_serialization=True)
    tokenizer = Tokenizer(
        WordLevel(
            {
                "<pad>": 0,
                "<bos>": 1,
                "<eos>": 2,
                "<unk>": 3,
                "Classify": 4,
                "synthetic": 5,
                "fruit": 6,
                "A": 7,
                "B": 8,
                "example": 9,
                "input": 10,
                "output": 11,
            },
            unk_token="<unk>",
        )
    )
    tokenizer.pre_tokenizer = Whitespace()
    wrapped = PreTrainedTokenizerFast(
        tokenizer_object=tokenizer,
        unk_token="<unk>",
        bos_token="<bos>",
        eos_token="<eos>",
        pad_token="<pad>",
    )
    wrapped.save_pretrained(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    create(parser.parse_args().destination)
