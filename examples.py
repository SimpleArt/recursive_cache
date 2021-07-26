from recursive_cache import recursive_cache

@recursive_cache
def fib(n):
    """Testing if it can evaluate deep recursion."""
    return n if n < 2 else fib(n-2) + fib(n-1)

@recursive_cache
def foo(n):
    """Testing if it can handle exceptions bouncing between multiple recursive functions."""
    if n < 0:
        raise ValueError
    return bar(n-1)

@recursive_cache
def bar(n):
    """Test helper for foo."""
    return foo(n-1)

@recursive_cache
def func1(n):
    if n <= 0:
        raise ValueError
    return func2(n, n)

@recursive_cache
def func2(m, n):
    if n <= 0:
        return func1(m-1)
    return func3(m, n-1)

@recursive_cache
def func3(m, n):
    return func2(m, n-1)

print(fib(3000))
print(foo(3000))
print(func1(3000))
