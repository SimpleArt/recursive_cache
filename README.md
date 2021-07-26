Python is well known for its conservative restriction on the amount of recursive calls one may have at any given moment. One way to work around this is to cache results to avoid recursing deeper when unnecessary. The issue with this approach is that you need to start finishing recursive calls and caching their results before the amount of recursive calls becomes too large. This module seeks to provide an alternative caching mechanism which allows deep recursion that goes well beyond the recursion limit.

Other examples may be seen in the `examples.py` file.

```python
@recursive_cache
def fib(n):
    return n if n < 2 else fib(n-2) + fib(n-1)

print(fib(3000))
```

Additionally, exception traceback is reduced by default to make recursive tracebacks more understandable.

Iterators, such as generators, cannot be easily handled. This is because they are lazily evaluated and wait until a value is requested to begin computation. Furthermore, they are one-time-use objects which must be re-evaluated on every call. However, this does mean deep recursion with iterators is impossible. It just means that any deep recursion must be taken care of *before* the iterator starts. This might mean using `itertools.chain` and generator expressions instead of generators.

For example, the following generator does not successfully run due to blocking recursion when used:

```python
from number_theory import is_prime

@recursive_cache
def generator(n):
    if n >= 0:
        for i in range(n):
            if is_prime(i):
                yield i
        yield from generator(n-1)
```

The following is a corrected version using `filter` which performs the recursion greedily while still allowing mostly lazy iterators:

```python
from itertools import chain
from number_theory import is_prime

@recursive_cache
def generator(n):
    if n >= 0:
        return chain(filter(is_prime, range(n)), generator(n-1))
    return iter([])
```

Or using generator expressions:

```python
from itertools import chain
from number_theory import is_prime

@recursive_cache
def generator(n):
    if n >= 0:
        return chain((i for i in range(n) if is_prime(i)), generator(n-1))
    return iter([])
```
