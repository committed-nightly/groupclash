"""A symbolic evaluator for the ``${{ }}`` expressions in a concurrency group.

The group is a string, and the only thing that matters about it is what
*varies*. ``ci-${{ github.ref }}`` is a different group per branch;
``ci-${{ github.head_ref }}`` is the same group for every branch, because on a
push there is no head ref and it expands to nothing. Those two lines look the
same and read the same. The only way to tell them apart is to evaluate them,
and the only way to evaluate them is to know which event you are evaluating
under.

So nothing here produces a string. Each part of the group becomes a `Value`
carrying a *kind* -- what it varies with -- and the rest of the tool reasons
about kinds:

    EMPTY       not defined for this event, expands to nothing
    FIXED       known text, the same for every run
    OTHER       non-empty, text unknown, but not different per branch or PR
    PER_REF     different per branch or pull request -- the useful one
    PER_COMMIT  different per push, but shared between workflows on one commit
    PER_RUN     different for every single run, so nothing ever shares it
    UNKNOWN     could not be decided

UNKNOWN is load-bearing. An expression this does not understand produces
UNKNOWN, the checks decline to run, and the CLI says which ones it skipped.
Guessing here would mean telling someone their CI is cancelling itself when it
is not, and that is a worse failure than staying quiet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EMPTY = "empty"
FIXED = "fixed"
BOOL = "bool"
OTHER = "other"
PER_REF = "per-ref"
PER_COMMIT = "per-commit"
PER_RUN = "per-run"
UNKNOWN = "unknown"

#: Kinds whose text we know exactly.
KNOWN_TEXT = (FIXED, BOOL, EMPTY)

#: Kinds that make a group different for every run, so two runs can never
#: share it -- which also means `cancel-in-progress` can never fire.
UNIQUE_PER_RUN = (PER_RUN,)

#: Kinds that distinguish one branch or pull request from another. A group
#: containing one of these is doing the job people think concurrency does.
DISCRIMINATING = (PER_REF,)


@dataclass(frozen=True)
class Value:
    """One evaluated piece of a group expression."""

    kind: str
    text: str = ""
    #: The context path this came from (`github.ref`), for error messages and
    #: for deciding whether two groups are the same expression.
    source: str = ""
    #: True when this value was reached because something to its left in an
    #: `||` was empty. `github.head_ref || github.run_id` on a push is a
    #: deliberate opt-out of concurrency, not an accident, and the checks
    #: treat it differently from a bare `github.run_id`.
    from_fallback: bool = False
    #: Set for numeric literals, where 0 is false and the string "0" is not.
    numeric: bool = False

    def render(self) -> str:
        """How this piece would read in a report."""
        if self.kind in KNOWN_TEXT:
            return self.text
        if self.source:
            return "${{ " + self.source + " }}"
        return "${{ ? }}"

    @property
    def identity(self) -> tuple[str, str]:
        """What makes two pieces the same piece, for collision comparison.

        Known text compares by text: two workflows called `CI` and `Release`
        both writing `${{ github.workflow }}` are *not* in the same group,
        and treating the expression as opaque would say they were. Everything
        else compares by where it came from, because two workflows reading
        `github.ref` on the same branch do get the same value.
        """
        if self.kind in KNOWN_TEXT:
            return ("text", self.text)
        return ("context", self.source or "?")


def truthiness(value: Value) -> bool | None:
    """Whether GitHub would treat this as true, or None if undecidable.

    Only `''` and `0` are false; every other string, `'false'` included, is
    true. That last one is why `BOOL` is a separate kind from `FIXED`.
    """
    if value.kind == EMPTY:
        return False
    if value.kind == BOOL:
        return value.text == "true"
    if value.kind == FIXED:
        if value.numeric:
            return float(value.text or 0) != 0
        return value.text != ""
    if value.kind == UNKNOWN:
        return None
    # OTHER, PER_REF, PER_COMMIT, PER_RUN: text we do not know, but all of
    # them are non-empty whenever they are defined at all.
    return True


# --------------------------------------------------------------------------
# A sequence of values is what an expression evaluates to. `format()` and
# plain concatenation can both produce several pieces at once, and collapsing
# them to one value early would lose the distinction between a group that has
# a varying part and one that does not.
# --------------------------------------------------------------------------

Seq = list


def flatten(seq: Seq) -> Seq:
    """Merge adjacent known text and drop empty literals.

    EMPTY values are *kept*, even though they contribute no text. They are the
    only record of which context came out blank, and "github.head_ref is not
    set on push" is the whole content of an empty-group finding.
    """
    out: Seq = []
    for value in seq:
        if value.kind in (FIXED, BOOL) and value.text == "":
            continue
        if out and out[-1].kind == FIXED and value.kind in (FIXED, BOOL):
            out[-1] = Value(FIXED, text=out[-1].text + value.text)
        elif value.kind == BOOL:
            out.append(Value(FIXED, text=value.text))
        else:
            out.append(value)
    return out


def collapse(seq: Seq) -> Value:
    """The single value an operator sees when given a sequence."""
    if not seq:
        return Value(EMPTY)
    if len(seq) == 1:
        return seq[0]
    if all(value.kind in KNOWN_TEXT for value in seq):
        return Value(FIXED, text="".join(value.text for value in seq))
    return Value(UNKNOWN)


class ExpressionError(ValueError):
    """An expression that could not be tokenised at all."""


TOKEN_RE = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<string>'(?:[^']|'')*')
    | (?P<number>\d+(?:\.\d+)?)
    | (?P<op>\|\||&&|==|!=|<=|>=|<|>|!|\(|\)|\[|\]|,|\.|\*)
    | (?P<ident>[A-Za-z_][A-Za-z0-9_-]*)
    """,
    re.VERBOSE,
)


def tokenise(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = TOKEN_RE.match(text, position)
        if match is None:
            raise ExpressionError(
                f"cannot read {text[position]!r} at character {position + 1}"
            )
        position = match.end()
        kind = match.lastgroup
        if kind == "space":
            continue
        assert kind is not None
        tokens.append((kind, match.group()))
    return tokens


class Parser:
    """Recursive descent over the subset of the expression language that

    turns up in concurrency groups: literals, context paths, `||`, `&&`,
    comparisons, `!`, parentheses and `format()`. Anything else parses
    successfully and evaluates to UNKNOWN -- an unrecognised function is not
    a syntax error, it is just something we cannot reason about.
    """

    def __init__(self, tokens: list[tuple[str, str]], resolve):
        self.tokens = tokens
        self.index = 0
        self.resolve = resolve

    # -- token helpers ------------------------------------------------------

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> tuple[str, str] | None:
        token = self.peek()
        if token is not None:
            self.index += 1
        return token

    def accept(self, value: str) -> bool:
        token = self.peek()
        if token is not None and token[1] == value:
            self.index += 1
            return True
        return False

    # -- grammar ------------------------------------------------------------

    def parse(self) -> Seq:
        seq = self.or_expr()
        if self.peek() is not None:
            # Trailing tokens mean we misread the shape of the expression.
            return [Value(UNKNOWN)]
        return seq

    def or_expr(self) -> Seq:
        left = self.and_expr()
        while self.accept("||"):
            right = self.and_expr()
            decided = truthiness(collapse(left))
            if decided is True:
                pass  # left wins, right never evaluated
            elif decided is False:
                left = [
                    Value(
                        value.kind,
                        value.text,
                        value.source,
                        from_fallback=True,
                        numeric=value.numeric,
                    )
                    for value in right
                ]
            else:
                left = [Value(UNKNOWN)]
        return left

    def and_expr(self) -> Seq:
        left = self.comparison()
        while self.accept("&&"):
            right = self.comparison()
            decided = truthiness(collapse(left))
            if decided is False:
                pass  # left is the falsy result
            elif decided is True:
                left = right
            else:
                left = [Value(UNKNOWN)]
        return left

    COMPARISONS = ("==", "!=", "<", "<=", ">", ">=")

    def comparison(self) -> Seq:
        left = self.unary()
        token = self.peek()
        if token is None or token[1] not in self.COMPARISONS:
            return left
        operator = self.take()[1]  # type: ignore[index]
        right = self.unary()
        return [compare(operator, collapse(left), collapse(right))]

    def unary(self) -> Seq:
        if self.accept("!"):
            decided = truthiness(collapse(self.unary()))
            if decided is None:
                return [Value(UNKNOWN)]
            return [Value(BOOL, text="false" if decided else "true")]
        return self.primary()

    def primary(self) -> Seq:
        token = self.take()
        if token is None:
            return [Value(UNKNOWN)]
        kind, text = token

        if text == "(":
            inner = self.or_expr()
            self.accept(")")
            return inner
        if kind == "string":
            return [Value(FIXED, text=text[1:-1].replace("''", "'"))]
        if kind == "number":
            return [Value(FIXED, text=text, numeric=True)]
        if kind != "ident":
            return [Value(UNKNOWN)]
        if text in ("true", "false"):
            return [Value(BOOL, text=text)]
        if self.accept("("):
            return self.call(text)
        return [self.path(text)]

    def path(self, head: str) -> Value:
        """A context reference: `github.event.pull_request.number`."""
        parts = [head]
        opaque = False
        while True:
            if self.accept("."):
                token = self.take()
                if token is None:
                    opaque = True
                    break
                parts.append(token[1])
                if token[1] == "*":
                    opaque = True
            elif self.accept("["):
                # Index expressions are consumed but not understood.
                depth = 1
                while depth and self.peek() is not None:
                    nxt = self.take()[1]  # type: ignore[index]
                    depth += (nxt == "[") - (nxt == "]")
                opaque = True
            else:
                break
        if opaque:
            return Value(UNKNOWN, source=".".join(parts))
        return self.resolve(".".join(parts))

    def call(self, name: str) -> Seq:
        args: list[Seq] = []
        if not self.accept(")"):
            while True:
                args.append(self.or_expr())
                if self.accept(","):
                    continue
                self.accept(")")
                break
        if name == "format":
            return format_call(args)
        return [Value(UNKNOWN, source=f"{name}()")]


def compare(operator: str, left: Value, right: Value) -> Value:
    """A comparison, decided only when both sides are known.

    GitHub compares strings case-insensitively, which is worth getting right
    here: `github.event_name == 'Push'` is true on a push.
    """
    if left.kind not in KNOWN_TEXT or right.kind not in KNOWN_TEXT:
        return Value(UNKNOWN)
    if operator in ("==", "!="):
        same = left.text.lower() == right.text.lower()
        return Value(BOOL, text="true" if (same == (operator == "==")) else "false")
    try:
        outcome = {
            "<": float(left.text) < float(right.text),
            "<=": float(left.text) <= float(right.text),
            ">": float(left.text) > float(right.text),
            ">=": float(left.text) >= float(right.text),
        }[operator]
    except ValueError:
        return Value(UNKNOWN)
    return Value(BOOL, text="true" if outcome else "false")


PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")


def format_call(args: list[Seq]) -> Seq:
    """`format('{0}-{1}', a, b)`, kept as separate pieces.

    Collapsing the result to one opaque value would throw away exactly the
    thing the tool is looking for, so the format string is split around its
    placeholders and the arguments are spliced in where they belong.
    """
    if not args:
        return [Value(UNKNOWN, source="format()")]
    template = collapse(args[0])
    if template.kind not in KNOWN_TEXT:
        return [Value(UNKNOWN, source="format()")]

    out: Seq = []
    position = 0
    for match in PLACEHOLDER_RE.finditer(template.text):
        out.append(Value(FIXED, text=template.text[position : match.start()]))
        index = int(match.group(1)) + 1
        if index < len(args):
            out.extend(args[index])
        else:
            # GitHub errors on this; we cannot know what it would produce.
            out.append(Value(UNKNOWN, source="format()"))
        position = match.end()
    out.append(Value(FIXED, text=template.text[position:]))
    return flatten(out)


HOLE_RE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)


def evaluate(template: str, resolve) -> Seq:
    """Evaluate a whole group string: literal text with `${{ }}` holes.

    `resolve` maps a context path to a `Value`, and is where the event comes
    in -- the same template resolves differently under `push` and under
    `pull_request`, which is the entire point.
    """
    out: Seq = []
    position = 0
    for match in HOLE_RE.finditer(template):
        out.append(Value(FIXED, text=template[position : match.start()]))
        try:
            out.extend(Parser(tokenise(match.group(1)), resolve).parse())
        except ExpressionError:
            out.append(Value(UNKNOWN, source=match.group(1).strip()))
        position = match.end()
    out.append(Value(FIXED, text=template[position:]))
    return flatten(out)
