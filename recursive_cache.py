from __future__ import annotations
import sys
from typing import overload, Any, Callable, Dict, Hashable, Iterable, Iterator, Mapping, NoReturn, Sequence, Tuple, TypeVar
from types import TracebackType
from functools import wraps
from itertools import groupby

E = TypeVar("E", bound=Exception)
S = TypeVar("S", bound=Hashable)
T = TypeVar("T")
TypedCallable = TypeVar("TypedCallable", bound=Callable[..., Any])  # arguments should be Hashable
KeyType = Tuple[Tuple[Hashable], Tuple[Tuple[str, Hashable]]]
HashifyType = Callable[[T, Dict[KeyType, T], KeyType], S]
UnhashifyType = Callable[[T, Dict[KeyType, T], KeyType], S]

# Global flag for toggling if the traceback should be stripped of anything from this module.
STRIP_TRACEBACK = True

toggle_message = f"""
  toggle {__name__}.STRIP_TRACEBACK to enable/disable reduced traceback.

  When enabled, the traceback is stripped of parts from {__name__}
  except for the last raise, repeated cycles of traceback are extracted out as chained
  exceptions, and the middle of the traceback is deleted so that only 90% of the
  sys.getrecursionlimit() is reached, since usually most of the traceback is repeating
  itself past that point. Note that tracebacks are limited to the most recent calls up
  to the system recursion limit, so deleting repeated information may be beneficial for
  seeing the original source of the exception.

  Note that chained recursion exceptions are in the form:

    recent traceback
    causes
    recursive traceback
    causes
    exception in original call

  which flattens out into:

    exception in original call
    recursive traceback
    recursive traceback
    recursive traceback
    ...
    recent traceback

  This helps you find the original call first at the end of the traceback and makes
  recursive calls more compact."""

class InfoException(Exception):
    """Used to raise from when stripping the traceback to provide additional information."""
    def __init__(self, msg: str = toggle_message, *args):
        super().__init__(msg, *args)

# True if this is the main call, False if this is a recursive call.
_base_call = True
# Store cached functions in the form:
# func_cache[func_id] = (func, cache)
_func_cache = list()
# Store calls that are currently being evaluated in the form:
# temp_cache[func_id, args, kwargs] = None
_temp_cache = dict()

def identity(x: T) -> T:
    return x

def equal_tracebacks(tb1: TracebackType, tb2: TracebackType) -> bool:
    """Compares two tracebacks."""
    return (
        tb1.tb_lasti == tb2.tb_lasti
        and tb1.tb_lineno == tb2.tb_lineno
        and all(
            getattr(tb1.tb_frame, f_attr) == getattr(tb2.tb_frame, f_attr)
            for f_attr in dir(tb1.tb_frame)
            if f_attr.startswith("f_")
            if f_attr not in {"f_back", "f_locals"}
        )
    )


def traceback_iter(tb: TracebackType) -> Iterator[TracebackType]:
    """Loops through the traceback, starting from source to most recent call."""
    while tb is not None:
        yield tb
        tb = tb.tb_next

def traceback_join(tbs: Iterable[TracebackType]) -> TracebackType:
    """Merges the traceback back together, starting from source to most recent call."""
    it = iter(tbs)
    root_tb = curr_tb = next(it, None)
    for tb in it:
        curr_tb.tb_next = tb
        curr_tb = tb
    curr_tb.tb_next = None
    return root_tb

def strip_traceback(e: E) -> E:
    """
    Strips the traceback of anything from this module,
    removes repeated cycles of traceback,
    and shortens the traceback if its too long.
    """
    # Strip the traceback of anything from this file.
    e.with_traceback(traceback_join(
        tb
        for tb in traceback_iter(e.__traceback__)
        if tb.tb_frame.f_code.co_filename != __spec__.origin
    ))
    tracebacks = list(traceback_iter(e.__traceback__))
    # Detect cycles in the traceback starting from the middle.
    for index in range(len(tracebacks) // 2, len(tracebacks) * 3 // 4):
        pointer = tracebacks[index]
        # Find the length of the cycle, assuming the next match is a cycle.
        cycle_start = next(
            (
                i
                for i in reversed(range(index))
                if equal_tracebacks(tracebacks[i], pointer)
            ),
            None,
        )
        # Cycle possibly exists.
        if cycle_start is not None:
            cycle = tracebacks[cycle_start:index]
            # Find all cycles.
            cycles = [
                i
                for i in range(len(tracebacks))
                if all(
                    equal_tracebacks(tb1, tb2)
                    for tb1, tb2 in zip(tracebacks[i:i+len(cycle)], cycle)
                )
            ]
            # Find the contiguous groups of cycles.
            subcycles = (cyc - len(cycle) * i for i, cyc in enumerate(cycles))
            grouped_cycles = [list(group) for _, group in groupby(cycles, lambda _: next(subcycles))]
            cause = None
            # Delete from the traceback in reversed order.
            for group in reversed(grouped_cycles):
                # Only pull out groups that are sufficiently long.
                if len(cycle) > 1 and len(group) > 3:
                    # Pull out the source of the exception that was called by the recursion.
                    if group[-1] + len(cycle) < len(tracebacks):
                        next_cause = InfoException(
                            f"[Previous lines caused an exception in the below recursion]"
                        ).with_traceback(traceback_join(tracebacks[group[-1]+len(cycle):]))
                        next_cause.__cause__ = cause
                        cause = next_cause
                    # Pull out the recursion.
                    next_cause = InfoException(
                        f"[Previous {len(cycle)} lines repeated {len(group)-1} more times "
                        f"and caused an exception in the code below which called it]"
                    ).with_traceback(traceback_join(tracebacks[group[0]:group[1]]))
                    next_cause.__cause__ = cause
                    cause = next_cause
                    # Cut it off of the tracebacks.
                    del tracebacks[group[0]:]
            # Nothing to chain?
            if cause is None:
                continue
            # Chain the exceptions together.
            base_cause = e
            while base_cause.__cause__ is not None:
                base_cause = base_cause.__cause__
            base_cause.__cause__ = cause
            break
    # If still too long, delete the middle portion.
    max_traceback = sys.getrecursionlimit() * 9 // 10
    if len(tracebacks) > max_traceback:
        del tracebacks[max_traceback // 2 : len(tracebacks) - max_traceback // 2]
    # Use the new tracebacks.
    return e.with_traceback(traceback_join(tracebacks))

def raise_exception(e: Exception) -> NoReturn:
    """Re-raise an exception."""
    raise e

def hashify(obj: T, cache: Dict[KeyType, T], key: KeyType) -> Tuple[Callable[[S], T], S]:
    """
    Store a shallow hashable copy of an object into the cache with the given key.

    Cases:
    - Use type(obj).copy(obj) if type(obj) has a copy method.
    - Hash Exception as obj itself. Unhash by raising obj.
    - Hash Hashable as obj itself.
    - Hash set as frozenset.
    - Hash Mapping as tuple(obj.items()). Unhash as dict(tuple).
    - Hash Iterator as obj. Unhash as iter(obj).
    - Hash Iterable or Sequence as tuple(obj). Unhash as tuple.
    - Otherwise just use the given object.
    """
    if hasattr(type(obj), "copy"):
        cache[key] = (type(obj).copy, obj)
    elif isinstance(obj, Exception):
        cache[key] = (raise_exception, obj)
    elif isinstance(obj, Hashable):
        cache[key] = (identity, obj)
    elif isinstance(obj, set):
        cache[key] = (set, frozenset(obj))
    elif isinstance(obj, Mapping):
        cache[key] = (dict, tuple(obj.items()))
    elif isinstance(obj, Iterator):
        cache[key] = (iter, obj)
    elif isinstance(obj, (Iterable, Sequence)):
        cache[key] = (identity, tuple(obj))
    else:
        cache[key] = (identity, obj)
    return cache[key]

def unhashify(obj: Tuple[Callable[[S], T], S], cache: Dict[KeyType, T], key: KeyType) -> T:
    """
    Return an unhashed object.

    If obj[1] is an Exception or Iterator, it is removed from the cache.
    """
    if isinstance(obj[1], (Exception, Iterator)):
        return obj[0](cache.pop(key)[1])
    return obj[0](obj[1])

@overload
def recursive_cache(*, hashify: HashifyType = hashify, unhashify: UnhashifyType = unhashify) -> Callable[[TypedCallable], TypedCallable]: ...
@overload
def recursive_cache(func: TypedCallable, *, hashify: HashifyType = hashify, unhashify: UnhashifyType = unhashify) -> TypedCallable: ...
def recursive_cache(*args, hashify = hashify, unhashify = unhashify):
    """
    Caches a function to avoid repeating completed function calls and handles
    recursive functions which require more calls than the call stack limit.

    Check help(hashify) to view the caching behavior.

    If the call stack limit is reached, function calls are cleared and the
    latest unfinished function call is re-called and cached if finished.

    Takes at most roughly double the amount of function calls to finish.

    Relies on RecursionError to work.
    """
    # Parse overloaded function.
    if len(args) == 0:
        return lambda func, /: recursive_cache(func, hashify, unhashify)
    elif len(args) > 1:
        raise TypeError(f"recursive_cache() takes 0 or 1 positional arguments but {len(args)} were given")
    func = args[0]
    # Already cached function.
    if any(func == f for f, cache in _func_cache):
        return func
    # Store results in the cache.
    cache = dict()
    func_id = len(_func_cache)
    _func_cache.append((func, cache))
    # Make the wrapper look like the given func.
    @wraps(func)
    def wrapper(*args: Hashable, **kwargs: Hashable) -> Any:
        global _base_call
        # Check if this is a recursive call or not.
        local_call = _base_call
        # Future calls must be recursive calls.
        _base_call = False
        # Break down the args and kwargs into tuple keys.
        key = (func_id, tuple(args), tuple(kwargs.items()))
        # Already running current call means infinite recursion.
        if key in _temp_cache:
            raise RecursionError("infinite recursion")
        # Store current call if we haven't computed it before.
        if key[1:] not in cache:
            _temp_cache[key] = None
        # Base call loop:
        # Re-calls the last call to continue making progress
        # while caching them to avoid recomputing
        # until every call is in the cache.
        while local_call and _temp_cache:
            # Get the last call that needs evaluating.
            temp_key = next(reversed(_temp_cache))
            func_key = temp_key[0]
            temp_args = temp_key[1:]
            temp_func, temp_func_cache = _func_cache[func_key]
            # Attempt the next function call.
            try:
                hashify(temp_func(*temp_args[0], **dict(temp_args[1])), temp_func_cache, temp_args)
            # If too many calls occur, reset the stack.
            except RecursionError as e:
                # If infinite recursion, stop.
                if str(e) == "infinite recursion":
                    _base_call = True
                    _temp_cache.clear()
                    raise e
                # If we make no further progress, stop.
                elif temp_key == next(reversed(_temp_cache)):
                    _base_call = True
                    _temp_cache.clear()
                    raise RecursionError("recursion blocked by other functions") from e
            # If an exception occurs, store it for re-raising and remove the call from the stack.
            except Exception as e:
                hashify(e, temp_func_cache, temp_args)
                del _temp_cache[temp_key]
            # If no exceptions occur, the temp_key succeeded and can be removed.
            else:
                del _temp_cache[temp_key]
        try:
            # If the key is already in the cache, unhash it.
            if key[1:] in cache:
                result = unhashify(cache[key[1:]], cache, key[1:])
            # Otherwise compute it.
            else:
                result = unhashify(hashify(func(*args, **kwargs), cache, key[1:]), cache, key[1:])
        # Don't do anything to RecursionError.
        except RecursionError as e:
            raise e
        # Other exceptions are the expected result of the function.
        # For example, they may be caught later.
        # Remove it from the cache and re-raise the exception.
        except Exception as e:
            if key in _temp_cache:
                del _temp_cache[key]
            # On the last raise before exiting the wrapper,
            # if toggled (which is by default),
            # then strip the traceback of anything from this module
            # and raise it from an additional custom info exception.
            if local_call and STRIP_TRACEBACK:
                e = strip_traceback(e)
                cause = e
                while cause.__cause__ is not None:
                    cause = cause.__cause__
                cause.__cause__ = InfoException()
                raise e
            # Otherwise just raise the exception normally.
            else:
                raise e
        # The result is properly finished.
        # Remove it from the cache and return the result.
        else:
            if key in _temp_cache:
                del _temp_cache[key]
            return result
        # Ensure the base call gets reset.
        finally:
            _base_call = local_call
    return wrapper
