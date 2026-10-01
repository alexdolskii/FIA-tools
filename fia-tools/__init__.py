import functools
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).parent


def _arguments(command):
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    from cli_arguments import parse_arguments
    return parse_arguments(command)


def _managed(function):
    @functools.wraps(function)
    def launch():
        _arguments(function.__name__)
        if str(_HERE) not in sys.path:
            sys.path.insert(0, str(_HERE))
        from run_resources import run_command
        return run_command(function.__name__, sys.argv[1:])
    return launch


def _load_script(filename):
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    spec = importlib.util.spec_from_file_location(
        filename, _HERE / f"{filename}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[filename] = mod
    spec.loader.exec_module(mod)
    return mod


@_managed
def select_channels():
    args = _arguments("select_channels")
    return _load_script("1_select_channels").select_channel_name(args.input)


@_managed
def generate_nuclei_mask():
    args = _arguments("generate_nuclei_mask")
    return _load_script("2_nuclei_mask_generation").main(args.input, args.particle_size)


@_managed
def generate_foci_mask():
    args = _arguments("generate_foci_mask")
    return _load_script("3_foci_mask_generation").main_filter_foci(args.input, args.foci_threshold)


@_managed
def quantify_foci():
    args = _arguments("quantify_foci")
    return _load_script("4_foci_quantification").main_summarize_res(args.input, njobs=args.jobs)


@_managed
def quantify_nuclear_intensity():
    args = _arguments("quantify_nuclear_intensity")
    return _load_script("nuclear_intensity").main(
        args.input, mode=args.input_type)


@_managed
def fia_collect_marker_intensity_results():
    args = _arguments("fia_collect_marker_intensity_results")
    return _load_script("collect_marker_intensity_results").main(args.input)


@_managed
def fia_marker_intensity_report():
    return _load_script("marker_intensity_report").main()


def fia_diagnostics():
    return _load_script('runtime_diagnostics').main()
