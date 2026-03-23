"""
Activation Condition Engine — Phase B.2

A self-contained DSL (domain-specific language) for goal activation conditions.
Four logical layers:

  1. **Tokenizer** — regex-based lexer that splits a condition string into typed
     tokens (GOAL_ATOM, EVENT_ATOM, AND, OR, LPAREN, RPAREN).
  2. **Parser** — recursive descent that builds an immutable AST from the token
     stream, following this grammar:

         expression := term (OR term)*
         term       := factor (AND factor)*
         factor     := LPAREN expression RPAREN | atom
         atom       := goal_atom | event_atom
         goal_atom  := "goal:" PYRAMID_ID ":" STATUS
         event_atom := "event:" EVENT_KEY

     AND binds tighter than OR.  Parentheses override precedence.
  3. **Evaluator** — walks the AST and resolves each leaf against live DB state
     (Goal.activation_status for goal atoms, LifeEvent.occurred for event atoms).
  4. **Auto-activation hook** — scans all PENDING goals for a user, evaluates
     their conditions, and flips matching goals to ACTIVE.  Supports one level
     of cascading re-scan.

Security guarantees:
  - No eval() / exec() / compile() — pure regex + recursive descent.
  - Token regex only accepts [A-Za-z0-9_] in identifiers.
  - Max parse depth of 10 prevents stack overflow from adversarial nesting.
  - Max condition length enforced at model layer (500 chars).
  - All DB queries use parameterised SQLModel — no string interpolation.
  - Malformed input is rejected at write time (validate_condition) so the
    evaluator never encounters unparseable strings at runtime.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from typing import Union

from sqlmodel import Session, col, select

from api.models import Goal, LifeEvent

logger = logging.getLogger(__name__)

# Maximum nesting depth for parenthesised sub-expressions.
_MAX_DEPTH = 10

# Maximum number of activation-check passes.  After activating goals in the
# first pass, we re-scan once to catch cascades (e.g. Goal A → ACTIVE causes
# Goal B's condition to become true).  Two passes is sufficient for any
# realistic pyramid; capped to prevent infinite loops.
_MAX_CASCADE_PASSES = 3


# ═══════════════════════════════════════════════════════════════════════════
# 1. Error type
# ═══════════════════════════════════════════════════════════════════════════


class ConditionParseError(ValueError):
    """Raised when an activation_condition string cannot be parsed.

    Inherits ValueError so it plays nicely with Pydantic field validators
    and can be caught generically by route handlers as a "bad input" error.
    """


# ═══════════════════════════════════════════════════════════════════════════
# 2. AST node types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class GoalCondition:
    """Leaf: true when the referenced goal's activation_status matches."""
    pyramid_id: str  # e.g. "S4" — looked up via Goal.pyramid_id
    status: str      # e.g. "completed" — compared case-insensitively


@dataclass(frozen=True)
class EventCondition:
    """Leaf: true when the referenced LifeEvent has occurred."""
    event_key: str  # e.g. "employed" — looked up via LifeEvent.event_key


@dataclass(frozen=True)
class AndCondition:
    """Branch: true when ALL children are true (short-circuits on first False)."""
    children: tuple[ConditionNode, ...]


@dataclass(frozen=True)
class OrCondition:
    """Branch: true when ANY child is true (short-circuits on first True)."""
    children: tuple[ConditionNode, ...]


# Union of all possible AST node types.
ConditionNode = Union[GoalCondition, EventCondition, AndCondition, OrCondition]


# ═══════════════════════════════════════════════════════════════════════════
# 3. Tokenizer
# ═══════════════════════════════════════════════════════════════════════════

# Token type constants — plain strings, no enum needed for six values.
_TT_GOAL_ATOM = "GOAL_ATOM"
_TT_EVENT_ATOM = "EVENT_ATOM"
_TT_AND = "AND"
_TT_OR = "OR"
_TT_LPAREN = "LPAREN"
_TT_RPAREN = "RPAREN"


@dataclass(frozen=True)
class _Token:
    """A single lexical token produced by the tokenizer."""
    type: str   # one of the _TT_* constants
    value: str  # the raw matched text
    pos: int    # character offset in the original string (for error messages)


# Combined regex for tokenisation.  Order matters: longer patterns first so
# "goal:S4:completed" isn't partially matched by a shorter alternative.
# Named groups map directly to token types.
_TOKEN_PATTERN = re.compile(
    r"(?P<GOAL_ATOM>goal:[A-Za-z0-9_]+:[A-Za-z_]+)"
    r"|(?P<EVENT_ATOM>event:[A-Za-z0-9_]+)"
    r"|(?P<AND>\bAND\b)"
    r"|(?P<OR>\bOR\b)"
    r"|(?P<LPAREN>\()"
    r"|(?P<RPAREN>\))"
    r"|(?P<WHITESPACE>\s+)"
)


def _tokenize(condition: str) -> list[_Token]:
    """Split *condition* into a list of tokens.

    Raises ConditionParseError on any character that doesn't match the
    recognised token patterns (including stray punctuation, quotes, etc.).
    """
    tokens: list[_Token] = []
    pos = 0

    for match in _TOKEN_PATTERN.finditer(condition):
        # Check for unrecognised characters between the end of the previous
        # match and the start of this one.
        if match.start() > pos:
            bad = condition[pos:match.start()]
            raise ConditionParseError(
                f"Unexpected character(s) {bad!r} at position {pos} "
                f"in condition: {condition!r}"
            )

        group_name = match.lastgroup
        # Named-group alternation always sets lastgroup for matches we keep.
        if group_name is None:
            raise ConditionParseError(
                f"Internal tokenizer error at position {match.start()!r}"
            )
        if group_name == "WHITESPACE":
            # Consume but don't emit.
            pos = match.end()
            continue

        # Map the regex group name to our internal token type constant.
        tt = {
            "GOAL_ATOM": _TT_GOAL_ATOM,
            "EVENT_ATOM": _TT_EVENT_ATOM,
            "AND": _TT_AND,
            "OR": _TT_OR,
            "LPAREN": _TT_LPAREN,
            "RPAREN": _TT_RPAREN,
        }[group_name]

        tokens.append(_Token(type=tt, value=match.group(), pos=match.start()))
        pos = match.end()

    # Check for trailing unrecognised characters.
    if pos < len(condition):
        bad = condition[pos:]
        raise ConditionParseError(
            f"Unexpected character(s) {bad!r} at position {pos} "
            f"in condition: {condition!r}"
        )

    return tokens


# ═══════════════════════════════════════════════════════════════════════════
# 4. Recursive descent parser
# ═══════════════════════════════════════════════════════════════════════════


class _Parser:
    """Stateful recursive descent parser that consumes a token list.

    The parser tracks a cursor (_pos) and a nesting depth counter (_depth).
    Grammar (AND binds tighter than OR):

        expression := term (OR term)*
        term       := factor (AND factor)*
        factor     := LPAREN expression RPAREN | atom
        atom       := GOAL_ATOM | EVENT_ATOM
    """

    def __init__(self, tokens: list[_Token], raw: str) -> None:
        self._tokens = tokens
        self._raw = raw        # original string, kept for error messages
        self._pos = 0
        self._depth = 0

    # ── helpers ────────────────────────────────────────────────────────

    def _peek(self) -> _Token | None:
        """Return the current token without consuming it, or None at EOF."""
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _advance(self) -> _Token:
        """Consume and return the current token.  Raises on unexpected EOF."""
        tok = self._peek()
        if tok is None:
            raise ConditionParseError(
                f"Unexpected end of condition: {self._raw!r}"
            )
        self._pos += 1
        return tok

    def _expect(self, token_type: str) -> _Token:
        """Consume the next token, asserting it has the expected type."""
        tok = self._advance()
        if tok.type != token_type:
            raise ConditionParseError(
                f"Expected {token_type} but got {tok.type} ({tok.value!r}) "
                f"at position {tok.pos} in condition: {self._raw!r}"
            )
        return tok

    # ── grammar rules ─────────────────────────────────────────────────

    def parse(self) -> ConditionNode:
        """Entry point: parse the full token stream into an AST."""
        if not self._tokens:
            raise ConditionParseError("Empty condition string.")
        node = self._parse_expression()
        # Ensure we consumed all tokens — leftover tokens mean a syntax error.
        leftover = self._peek()
        if leftover is not None:
            raise ConditionParseError(
                f"Unexpected token {leftover.value!r} at position {leftover.pos} "
                f"after complete expression in condition: {self._raw!r}"
            )
        return node

    def _parse_expression(self) -> ConditionNode:
        """expression := term (OR term)*"""
        left = self._parse_term()
        terms = [left]

        while self._peek() and self._peek().type == _TT_OR:  # type: ignore[union-attr]
            self._advance()  # consume OR
            terms.append(self._parse_term())

        if len(terms) == 1:
            return terms[0]
        return OrCondition(children=tuple(terms))

    def _parse_term(self) -> ConditionNode:
        """term := factor (AND factor)*"""
        left = self._parse_factor()
        factors = [left]

        while self._peek() and self._peek().type == _TT_AND:  # type: ignore[union-attr]
            self._advance()  # consume AND
            factors.append(self._parse_factor())

        if len(factors) == 1:
            return factors[0]
        return AndCondition(children=tuple(factors))

    def _parse_factor(self) -> ConditionNode:
        """factor := LPAREN expression RPAREN | atom"""
        tok = self._peek()
        if tok is None:
            raise ConditionParseError(
                f"Unexpected end of condition (expected atom or '('): {self._raw!r}"
            )

        if tok.type == _TT_LPAREN:
            self._depth += 1
            if self._depth > _MAX_DEPTH:
                raise ConditionParseError(
                    f"Nesting depth exceeds maximum of {_MAX_DEPTH} "
                    f"in condition: {self._raw!r}"
                )
            self._advance()  # consume '('
            node = self._parse_expression()
            self._expect(_TT_RPAREN)
            self._depth -= 1
            return node

        return self._parse_atom()

    def _parse_atom(self) -> ConditionNode:
        """atom := GOAL_ATOM | EVENT_ATOM"""
        tok = self._advance()

        if tok.type == _TT_GOAL_ATOM:
            # Format: "goal:<pyramid_id>:<status>"
            parts = tok.value.split(":", 2)  # ["goal", pyramid_id, status]
            if len(parts) != 3 or not parts[1] or not parts[2]:
                raise ConditionParseError(
                    f"Malformed goal atom {tok.value!r} at position {tok.pos}. "
                    f"Expected format: goal:<pyramid_id>:<status>"
                )
            return GoalCondition(
                pyramid_id=parts[1],
                status=parts[2].lower(),
            )

        if tok.type == _TT_EVENT_ATOM:
            # Format: "event:<event_key>"
            parts = tok.value.split(":", 1)  # ["event", event_key]
            if len(parts) != 2 or not parts[1]:
                raise ConditionParseError(
                    f"Malformed event atom {tok.value!r} at position {tok.pos}. "
                    f"Expected format: event:<event_key>"
                )
            return EventCondition(event_key=parts[1])

        raise ConditionParseError(
            f"Expected a goal or event atom but got {tok.type} ({tok.value!r}) "
            f"at position {tok.pos} in condition: {self._raw!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. Public parse / validate functions
# ═══════════════════════════════════════════════════════════════════════════


def parse_condition(condition_str: str) -> ConditionNode:
    """Parse an activation_condition DSL string into an AST.

    Raises ConditionParseError if the string is syntactically invalid.
    """
    tokens = _tokenize(condition_str)
    parser = _Parser(tokens, raw=condition_str)
    return parser.parse()


def validate_condition(condition_str: str | None) -> ConditionNode | None:
    """Validate a condition string on write (create/update).

    Returns the parsed AST on success, or None if *condition_str* is
    None / empty (meaning "no condition — always activatable").

    Raises ConditionParseError for syntactically invalid input.  The B.3
    route layer should catch this and return HTTP 400 with the message.
    """
    if not condition_str or not condition_str.strip():
        return None
    return parse_condition(condition_str.strip())


# ═══════════════════════════════════════════════════════════════════════════
# 6. Evaluator
# ═══════════════════════════════════════════════════════════════════════════


def evaluate_condition(
    node: ConditionNode,
    session: Session,
    user_id: str,
) -> bool:
    """Evaluate a parsed condition AST against live DB state.

    Resolution rules:
      - GoalCondition:  look up Goal by (user_id, pyramid_id).
                        True if found and activation_status matches (case-insensitive).
                        False if the goal doesn't exist (safe default).
      - EventCondition: look up LifeEvent by (user_id, event_key).
                        True if found and occurred == True.
                        False if the event row doesn't exist or occurred is False.
      - AndCondition:   all children must be True (short-circuits on first False).
      - OrCondition:    at least one child must be True (short-circuits on first True).
    """
    if isinstance(node, GoalCondition):
        goal = session.exec(
            select(Goal)
            .where(Goal.user_id == user_id)
            .where(Goal.pyramid_id == node.pyramid_id)
        ).first()
        if goal is None:
            return False
        return (goal.activation_status or "").lower() == node.status

    if isinstance(node, EventCondition):
        event = session.exec(
            select(LifeEvent)
            .where(LifeEvent.user_id == user_id)
            .where(LifeEvent.event_key == node.event_key)
        ).first()
        if event is None:
            return False
        return bool(event.occurred)

    if isinstance(node, AndCondition):
        return all(evaluate_condition(child, session, user_id) for child in node.children)

    if isinstance(node, OrCondition):
        return any(evaluate_condition(child, session, user_id) for child in node.children)

    raise TypeError(f"Unknown condition node type: {type(node)}")


# ═══════════════════════════════════════════════════════════════════════════
# 7. Auto-activation hook
# ═══════════════════════════════════════════════════════════════════════════


def check_and_update_activations(
    session: Session,
    user_id: str,
) -> list[Goal]:
    """Scan PENDING goals, evaluate conditions, activate those now satisfied.

    Called after:
      - A goal's activation_status changes to COMPLETED (B.3 update route)
      - A LifeEvent is marked occurred=True (B.3 life events route)

    Supports cascading: after the first pass activates goals, a re-scan catches
    any goals whose conditions depended on the newly-activated ones.  Capped at
    _MAX_CASCADE_PASSES to prevent infinite loops (impossible in practice since
    conditions reference status values, not transitions).

    Returns the full list of goals that were transitioned to ACTIVE across all
    passes.  Does NOT commit — the caller is responsible for committing the
    session (so it can be wrapped in a larger transaction if needed).
    """
    all_activated: list[Goal] = []

    for pass_num in range(_MAX_CASCADE_PASSES):
        # Fetch PENDING goals that have a condition to evaluate.
        pending = session.exec(
            select(Goal)
            .where(Goal.user_id == user_id)
            .where(Goal.activation_status == "PENDING")
            .where(col(Goal.activation_condition).isnot(None))
        ).all()

        activated_this_pass: list[Goal] = []

        for goal in pending:
            condition_str = (goal.activation_condition or "").strip()
            if not condition_str:
                continue

            try:
                node = parse_condition(condition_str)
            except ConditionParseError:
                logger.warning(
                    "Skipping unparseable activation_condition on goal %s "
                    "(pyramid_id=%s): %r",
                    goal.id, goal.pyramid_id, goal.activation_condition,
                )
                continue

            if evaluate_condition(node, session, user_id):
                goal.activation_status = "ACTIVE"
                goal.updated_at = datetime.datetime.now(datetime.UTC)
                session.add(goal)
                activated_this_pass.append(goal)
                logger.info(
                    "Auto-activated goal %s (%s / %s): condition %r satisfied",
                    goal.id, goal.pyramid_id, goal.name, goal.activation_condition,
                )

        all_activated.extend(activated_this_pass)

        if not activated_this_pass:
            # Nothing new activated — no point re-scanning.
            break

        # Flush so subsequent passes see the updated activation_status values
        # when evaluating conditions that reference goals activated this pass.
        session.flush()

        logger.debug(
            "Activation pass %d: %d goal(s) activated, re-scanning for cascades",
            pass_num + 1, len(activated_this_pass),
        )

    return all_activated
