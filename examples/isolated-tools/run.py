"""Source-matched independent sample; provider replies are synthetic."""

import argparse

from cutback.demo import launch_demo

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--tool-image-id", required=True)
parser.add_argument("--port", type=int, default=8766)
args = parser.parse_args()
launch_demo(port=args.port, tool_image_id=args.tool_image_id)
