import argparse
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).parent


def _load_script(filename):
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    spec = importlib.util.spec_from_file_location(
        filename, _HERE / f"{filename}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def select_channels():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', type=str, required=True)
    args = parser.parse_args()
    _load_script("1_select_channels").select_channel_name(args.input)


def generate_nuclei_mask():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', type=str, required=True)
    parser.add_argument('-p', '--particle_size', type=int, default=2500)
    args = parser.parse_args()
    _load_script("2_nuclei_mask_generation").main(args.input, args.particle_size)


def generate_foci_mask():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', type=str, required=True)
    parser.add_argument('-f', '--foci_threshold', type=int, default=150)
    args = parser.parse_args()
    _load_script("3_foci_mask_generation").main_filter_foci(args.input, args.foci_threshold)


def quantify_foci():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', type=str, required=True)
    parser.add_argument('-j', '--jobs', type=int, default=4)
    args = parser.parse_args()
    _load_script("4_foci_quantification").main_summarize_res(args.input, njobs=args.jobs)
