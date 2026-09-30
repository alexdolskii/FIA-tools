"""Validated terminal prompts shared by the FIA commands."""

import logging
from functools import wraps


class Cancelled(Exception):
    """The user requested cancellation at an interactive prompt."""


CANCELLATION_EXCEPTIONS = (Cancelled, KeyboardInterrupt, EOFError)


def read_answer(prompt, default=None):
    """Normalize input; apply only an explicitly advertised default."""
    answer = input(prompt).strip().lower()
    if answer == 'q':
        raise Cancelled()
    return default if answer == '' and default is not None else answer


def ask_integer(prompt, minimum, maximum=None):
    """Retry the current question until an integer in the allowed range is entered."""
    allowed = (f'from {minimum} to {maximum}' if maximum is not None
               else f'greater than or equal to {minimum}')
    while True:
        answer = read_answer(prompt)
        try:
            value = int(answer)
        except ValueError:
            print(f'Invalid input. Enter a whole number {allowed}, or q.')
            continue
        if value < minimum or (maximum is not None and value > maximum):
            print(f'Choice must be {allowed}. Enter a valid number, or q.')
            continue
        return value


def ask_choice(prompt, choices, default=None, error=None):
    """Map a displayed choice to its value without treating typos as a default."""
    while True:
        answer = read_answer(prompt, default)
        if answer in choices:
            return choices[answer]
        print(error or f'Please enter {", ".join(choices)}, or q.')


def ask_yes_no(prompt, default=None):
    """Accept explicit yes/no answers, or a documented Enter default."""
    fallback = None if default is None else ('yes' if default else 'no')
    return ask_choice(prompt, {'yes': True, 'y': True, 'no': False, 'n': False},
                      default=fallback, error='Please enter yes, no, or q.')


def parse_selection(answer, count, allow_none=False):
    """Parse a one-based comma-separated selection into unique zero-based indices."""
    answer = answer.strip().lower()
    if answer == 'q':
        raise Cancelled()
    if answer in ('a', 'all'):
        return list(range(count))
    if allow_none and answer in ('n', 'none'):
        return []
    options = 'numbers, all, none, or q' if allow_none else 'numbers, all, or q'
    try:
        numbers = [int(value.strip()) for value in answer.split(',')]
    except ValueError as error:
        raise ValueError(f'Enter comma-separated {options}.') from error
    if not numbers or any(number < 1 or number > count for number in numbers):
        raise ValueError(f'Choose numbers between 1 and {count}.')
    return sorted({number - 1 for number in numbers})


def choose_indices(prompt, count, allow_none=False):
    """Repeat a multiple-choice prompt while preserving earlier selections."""
    while True:
        answer = read_answer(prompt)
        try:
            return parse_selection(answer, count, allow_none)
        except ValueError as error:
            print(error)


def cancelable(function):
    """Keep intentional cancellation separate from processing failures at the CLI."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except CANCELLATION_EXCEPTIONS:
            print('Analysis canceled by user.')
            # The legacy stages attach their journals to the root logger, which
            # may have no console handler. Record cancellation without printing
            # it twice or changing global logging settings.
            record = logging.LogRecord(function.__module__, logging.WARNING,
                                       function.__code__.co_filename, 0,
                                       'CANCELLED | Analysis canceled by user.', (), None)
            for handler in logging.getLogger().handlers:
                if isinstance(handler, logging.FileHandler) and record.levelno >= handler.level:
                    handler.handle(record)
            return 130
    return wrapped
