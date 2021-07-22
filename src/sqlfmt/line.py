from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from sqlfmt.token import Token, TokenType, split_after


@dataclass
class Node:
    token: Token
    previous_node: Optional["Node"]
    inherited_depth: int
    prefix: str
    value: str
    depth: int
    change_in_depth: int
    open_brackets: List[Token] = field(default_factory=list)

    def __str__(self) -> str:
        return self.prefix + self.value

    def __repr__(self) -> str:
        """
        Because of self.previous_node, the default dataclass repr creates
        unusable output
        """
        prev = (
            f"Node(token={self.previous_node.token})" if self.previous_node else "None"
        )
        b = [str(t) for t in self.open_brackets]
        r = (
            f"Node(\n"
            f"\ttoken='{str(self.token)}',\n"
            f"\tprevious_node={prev},\n"
            f"\tinherited_depth={self.inherited_depth},\n"
            f"\tdepth={self.depth},\n"
            f"\tchange_in_depth={self.change_in_depth},\n"
            f"\tprefix='{self.prefix}',\n"
            f"\tvalue='{self.value}',\n"
            f"\topen_brackets={b}\n"
            f")"
        )
        return r

    def __len__(self) -> int:
        return len(str(self))

    @classmethod
    def from_token(cls, token: Token, previous_node: Optional["Node"]) -> "Node":
        """
        Create a Node from a Token. For the Node to be properly formatted,
        method call must also include a reference to the previous Node in the
        Query (either on the same or previous Line), unless it is the first
        Node in the Query.

        The Node's depth and whitespace are calculated when it is created
        (this does most of the formatting of the Node). Node values are
        lowercased if they are simple names, keywords, or statements.
        """
        if previous_node:
            inherited_depth = previous_node.depth + previous_node.change_in_depth
            open_brackets = previous_node.open_brackets.copy()
        else:
            inherited_depth = 0
            open_brackets = []

        depth, change_in_depth, open_brackets = cls.calculate_depth(
            token, inherited_depth, open_brackets
        )

        previous_token: Optional[Token] = None
        if previous_node is None or previous_node.token.type == TokenType.NEWLINE:
            is_first_on_line = True
        else:
            is_first_on_line = False
            previous_token = previous_node.token

        prefix = cls.whitespace(token, is_first_on_line, depth, previous_token)
        value = cls.capitalize(token)

        return Node(
            token,
            previous_node,
            inherited_depth,
            prefix,
            value,
            depth,
            change_in_depth,
            open_brackets,
        )

    @classmethod
    def calculate_depth(
        cls, token: Token, inherited_depth: int, open_brackets: List[Token]
    ) -> Tuple[int, int, List[Token]]:
        """
        Calculates the depth statistics for a Node (depth, change_in_depth,
        open_brackets), based on the properties of its token. Depth determines
        indentation and line splitting in the formatted query.

        We're operating on a single token at a time; since the token can affect
        its own node's indentation and/or the indentation of the next node, we
        need to start with the "inherited" depth and then adjust the final
        depth of the node based on the contents of the token at the node.
        """
        change_before = 0
        change_after = 0

        if token.type == TokenType.TOP_KEYWORD:
            maybe_last_bracket: Optional[Token] = (
                open_brackets.pop() if open_brackets else None
            )
            if (
                maybe_last_bracket and maybe_last_bracket.type == TokenType.TOP_KEYWORD
            ):  # this is a kw like 'from' that follows another top keyword,
                # so we need to dedent
                change_before = -1
            elif (
                maybe_last_bracket
            ):  # it's an open paren that needs to go back on the stack
                open_brackets.append(maybe_last_bracket)

            open_brackets.append(token)
            change_after = 1

        elif token.type == TokenType.BRACKET_OPEN:
            open_brackets.append(token)
            change_after = 1

        elif token.type == TokenType.BRACKET_CLOSE:
            try:
                last_bracket: Token = open_brackets.pop()
                change_before = -1
                # if the closing bracket follows a keyword like "from",
                # we need to pop the next open bracket off the stack,
                # which should be the matching pair to the current token
                if last_bracket and last_bracket.type == TokenType.TOP_KEYWORD:
                    last_bracket = open_brackets.pop()
                    change_before -= 1
            except IndexError:
                raise ValueError(
                    f"Closing bracket '{token.token}' found at "
                    f"{token.spos} before bracket was opened."
                )
            matches = {
                "{": "}",
                "(": ")",
                "[": "]",
            }
            assert (
                last_bracket.type == TokenType.BRACKET_OPEN
                and matches[last_bracket.token] == token.token
            ), (
                f"Closing bracket '{token.token}' found at {token.spos} does not match "
                f"last opened bracket '{last_bracket.token}' found at "
                f"{last_bracket.spos}."
            )

        elif token.type == TokenType.STATEMENT_START:
            change_after = 1
        elif token.type == TokenType.STATEMENT_END:
            change_before = -1

        depth = inherited_depth + change_before

        return depth, change_after, open_brackets

    @classmethod
    def whitespace(
        cls,
        token: Token,
        is_first_on_line: bool,
        depth: int,
        previous_token: Optional[Token],
    ) -> str:
        """
        Returns the proper whitespace before the token literal, to be set as the
        prefix of the Node.

        Most tokens should be prefixed by a simple space. Other cases are outlined
        below.
        """
        NO_SPACE = ""
        SPACE = " "
        INDENT = SPACE * 4

        if is_first_on_line:
            return INDENT * depth
        # tokens that are never preceded by a space
        elif token.type in (
            TokenType.BRACKET_CLOSE,
            TokenType.DOUBLE_COLON,
            TokenType.COMMA,
            TokenType.DOT,
            TokenType.NEWLINE,
        ):
            return NO_SPACE
        # names preceded by dots are namespaced identifiers. No space.
        elif (
            token.type in (TokenType.QUOTED_NAME, TokenType.NAME)
            and previous_token
            and previous_token.type == TokenType.DOT
        ):
            return NO_SPACE
        # open brackets that follow names are function calls
        # (no space) unless the preceding name is "as"
        # (declaring a CTE) or "over" (declaring a window partition)
        elif (
            token.type == TokenType.BRACKET_OPEN
            and previous_token
            and previous_token.type == TokenType.NAME
            and previous_token.token.lower() not in ("over", "as")
        ):
            return NO_SPACE
        elif token.type == TokenType.BRACKET_OPEN:
            return SPACE
        # no spaces after an open bracket or a cast operator (::)
        elif previous_token and previous_token.type in (
            TokenType.BRACKET_OPEN,
            TokenType.DOUBLE_COLON,
        ):
            return NO_SPACE
        else:
            return SPACE

    @classmethod
    def capitalize(cls, token: Token) -> str:
        """
        Proper style is to lowercase all keywords, statements, and names.
        If DB identifiers can't be lowercased, they should be quoted. This
        will likely need to be changed for Snowflake support.
        """
        if token.type in (
            TokenType.TOP_KEYWORD,
            TokenType.NAME,
            TokenType.STATEMENT_START,
            TokenType.STATEMENT_END,
        ):
            return token.token.lower()
        else:
            return token.token


@dataclass
class Line:
    source_string: str
    previous_node: Optional[Node]  # last node of prior line, if any
    nodes: List[Node] = field(default_factory=list)
    depth: int = 0
    change_in_depth: int = 0
    open_brackets: List[Token] = field(default_factory=list)
    depth_split: Optional[int] = None
    first_comma: Optional[int] = None

    def append_token(self, token: Token) -> None:
        """
        Creates a new Node from the passed token and the context of the current line,
        then appends that Node to self.nodes and updates line depth and split features
        as necessary
        """
        previous_node: Optional[Node]
        if self.nodes:
            previous_node = self.nodes[-1]
        else:
            previous_node = self.previous_node

        node = Node.from_token(token, previous_node)

        # update the line's depth stats from the node
        if not self.nodes:
            self.depth = node.depth
            self.change_in_depth = node.change_in_depth
            self.open_brackets = node.open_brackets
        else:
            self.change_in_depth = node.depth - self.depth + node.change_in_depth

        # splits should happen outside in... if this line is increasing depth,
        # we should split on the first node that increases depth. If it is
        # decreasing depth, we should split on the last node that decreases depth
        change_over_node = node.depth - node.inherited_depth + node.change_in_depth
        split_index = len(self.nodes)
        if split_after(node.token.type):
            split_index += 1

        if token.type == TokenType.COMMENT:
            self.depth_split = split_index
        if self.change_in_depth < 0 and change_over_node < 0 and split_index > 0:
            self.depth_split = split_index
        elif self.depth_split is None and node.change_in_depth > 0:
            self.depth_split = split_index

        if (
            token.type == TokenType.COMMA
            and node.open_brackets == self.open_brackets
            and self.first_comma is None
        ):
            self.first_comma = split_index

        self.nodes.append(node)

    def append_newline(self) -> None:
        """
        Create a new NEWLINE token and append it to the end of this line
        """
        previous_token: Optional[Token] = None
        if self.nodes:
            previous_token = self.nodes[-1].token
        elif self.previous_node:
            previous_token = self.previous_node.token

        if previous_token:
            spos = (previous_token.epos[0], previous_token.epos[1] + 1)
            epos = (previous_token.epos[0], previous_token.epos[1] + 2)
            source_line = previous_token.line
        else:
            spos = (0, 0)
            epos = (0, 1)
            source_line = ""

        nl = Token(
            type=TokenType.NEWLINE,
            prefix="",
            token="\n",
            spos=spos,
            epos=epos,
            line=source_line,
        )
        self.append_token(nl)

    @classmethod
    def from_nodes(
        cls, source_string: str, previous_node: Optional[Node], nodes: List[Node]
    ) -> "Line":
        """
        Creates and returns a new line from a list of Nodes. Useful for line
        splitting and merging
        """
        line = Line(
            source_string=source_string,
            previous_node=previous_node,
            depth=previous_node.depth + previous_node.change_in_depth
            if previous_node
            else 0,
        )
        for node in nodes:
            line.append_token(node.token)  # todo: optimize this.

        return line

    @property
    def tokens(self) -> List[Token]:
        tokens = []
        for node in self.nodes:
            tokens.append(node.token)
        return tokens

    @property
    def starts_with_top_keyword(self) -> bool:
        if not self.nodes:
            return False
        elif self.nodes[0].token.type == TokenType.TOP_KEYWORD:
            return True
        else:
            return False

    @property
    def ends_with_comma(self) -> bool:
        if not self.nodes:
            return False
        elif self.nodes[-1].token.type == TokenType.COMMA:
            return True
        elif (
            len(self.nodes) > 1
            and self.nodes[-1].token.type == TokenType.NEWLINE
            and self.nodes[-2].token.type == TokenType.COMMA
        ):
            return True
        else:
            return False

    @property
    def ends_with_comment(self) -> bool:
        if not self.nodes:
            return False
        elif self.nodes[-1].token.type == TokenType.COMMENT:
            return True
        elif (
            len(self.nodes) > 1
            and self.nodes[-1].token.type == TokenType.NEWLINE
            and self.nodes[-2].token.type == TokenType.COMMENT
        ):
            return True
        else:
            return False

    def __str__(self) -> str:
        if self.nodes:
            parts = []
            for node in self.nodes:
                parts.append(str(node))
            return "".join(parts)
        else:
            return ""

    def __len__(self) -> int:
        return len(str(self))
