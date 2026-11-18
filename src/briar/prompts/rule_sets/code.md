# code

Python style rules for the lines a task adds or changes. A repo turns this set on with `rules: [code]`
in its runbook; it is skipped in a repo with no Python manifest.

## Baseline
Python code you add or change follows these rules:

- Every function lives inside a class. A module holds imports, constants, classes and one call in the `__main__` guard. A test file may keep its `test_` functions and fixtures.
- A class has one job. The `__init__` only assigns what it receives. Collaborators (clients, repositories, loggers) are passed in, never built inside a method.
- A method has at most 20 lines and a class at most 200. Nesting stays at two levels: use a guard clause, or move the step into a class.
- No `elif` chain and no `else` after a `return`: use a dict of classes, polymorphism or a strategy.
- Type hints on every parameter and on the return. At most 3 parameters; more means a pydantic model or a frozen dataclass. No boolean flag parameter: write two methods. No `*args` or `**kwargs`.
- One type per variable: `X | None` only when absence is a real state. Use an `Enum` for a fixed set of values, not a string or a `Literal`.
- Catch the specific exception, never a bare `except` or `except Exception`. Raise your own exception classes. Do not return None to say that something failed.
- No magic number or string: name it as an UPPER_CASE constant. No `print()`: use the injected logger. No commented out code and no dead code.
- A name says what a thing is for. A comment says why, never what. Add no abstraction, parameter or option "just in case".

## Review
Check every line the diff adds or changes in a `.py` file against this list.

### Mechanical rules
1. Every function lives inside a class. A module holds only imports, constants, classes and one call in the `__main__` guard.
2. Type hints on every parameter and on the return.
3. No boolean flag parameter: split it into two methods.
4. A method has at most 20 lines and a class at most 200 lines.
5. No nesting deeper than 2 levels: use guard clauses or a collaborator class.
6. No `elif` chain: use polymorphism, a dict registry or strategies.
7. No `else` after `return`, `raise`, `continue` or `break`.
8. No `@staticmethod`: use an instance method or a value object.
9. No `*args` or `**kwargs`: declare typed parameters. No more than 3 parameters: use a pydantic model for outside input or a dataclass for an internal value.
10. One type per variable: only `X | None`, never a union of several types. An `Enum`, not a `Literal`.
11. No bare `except` and no `except Exception`: catch the specific exception.
12. No `print()`: use an injected logger.
13. No magic number: name it as an UPPER_CASE constant.
14. No `== True`, `== False` or `== None`: use the value itself or `is None`.
15. No double negative in a condition: write the positive one.
16. No commented out code.
17. Dataclasses are frozen: `@dataclass(frozen=True)`.
18. A name that says nothing (`data`, `tmp`, `handle`, `process`, `manager`) is wrong: name the intent.
19. The `__init__` only assigns. No logic, I/O or work in it.
20. No module or class named `utils` or `helpers`: the code belongs in a class with one job.

### Judgment rules
1. One class, one job. A class that mixes validation, persistence, sending, reporting and formatting is split, one class per job.
2. Collaborators arrive through `__init__`. A method that builds its own service, client, repository or connection is wrong. Value objects, dataclasses, pydantic models and exceptions may be built anywhere.
3. No I/O in a class that holds a business rule: database, HTTP, files, subprocess, environment and clock live in an adapter class that is injected.
4. Small interfaces: no class implements a method it does not need (a stub that raises `NotImplementedError` or does `pass` to fit a fat base).
5. A subclass keeps the contract of its parent. Inherit only for a real is-a, never to reuse helper methods: compose instead.
6. A comment says why, never what. A comment that narrates the code means the code needs a better name.
7. Data from outside (API, CLI, config, files) goes through a pydantic model, and the checks happen there, at the edge. Internal value objects are frozen dataclasses.
8. `X | None` only when absence is a valid state. A lookup that can fail raises its own exception.
9. Errors are own classes under a project base error. An `Enum` for every state or category, not a bare string.
10. No mutation of inputs: return a new value. No state a class does not need.
11. Simplify first. No abstraction, factory, generic framework, parameter, config option or method added "just in case". A pattern only when it removes complexity that exists today.
12. No dead code: an unused method, branch or parameter added by the diff.
13. Testable by design. A class that needs patching or heavy mocks to be tested is wrong: inject the collaborator.

### Where these rules give way
- "Depend on a Protocol, never on a concrete class" applies when a second implementation exists, or when the collaborator does I/O and a test needs a fake. Otherwise depend on the concrete class (simplify first).
- A `_method` is fine for a step with no meaning outside its class. A step that is a different job becomes a new injected class.
- A pydantic validator may raise `ValueError`: pydantic requires it.
- Test files keep module level `test_` functions and fixtures, and `if __name__ == "__main__":` may hold one call.
