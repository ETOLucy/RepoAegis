"""清洗 issue 的标题和正文：交给模型之前，删掉人在网页上看不见、模型却读得到的东西。只用标准库。

要删的有两类：
- 不可见字符：没有宽度、页面上什么都不显示的字符，比如"零宽空格"。它们能夹在正常文字中间，
  拼出一句人看不见的话。
- HTML 注释：写在 `<!--` 和 `-->` 之间的文字，GitHub 页面上不显示。issue 模板常用它写提示语，
  也能被人拿来藏指令。

代码块不动：用三个反引号 ``` 或 ~~~ 上下围起来的整段代码，以及一对反引号 `像这样` 括起来的行内代码，
里面的注释原样保留，因为那是代码的一部分。

怎么删，完全照搬两份源码，不做改动（2026-10-27 定，依据见 docs/调研/看不见的内容与清洗规则.md 第七节）：
- 正文：GitHub 官方 MCP 服务器（GitHub 自己提供给 AI 读 issue 的工具）的 `sanitize.Content`，删不可见字符；
  再用 gh-aw（GitHub 做的另一个 agent 项目）的 `applyToNonCodeRegions(removeXmlComments)`，删代码以外的注释。
- 标题：GitHub 官方 MCP 服务器的 `sanitize.PlainText`，删不可见字符、去掉 HTML 标签。

改写自以下两份 MIT 许可的源码：

- github/github-mcp-server `pkg/sanitize/sanitize.go`，提交 f38526264abb14961b32cea386e38ec6d3cf674f。
  Copyright (c) 2025 GitHub
- github/gh-aw `actions/setup/js/sanitize_content_core.cjs`，提交 deb2957f7df6915057215f2927abe65d1b644010。
  Copyright GitHub, Inc.

  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
  associated documentation files (the "Software"), to deal in the Software without restriction, including
  without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
  following conditions: The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.
  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
  LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
  EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
  IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
  USE OR OTHER DEALINGS IN THE SOFTWARE.

MCP 服务器是用 Go 语言写的，gh-aw 是用 JavaScript 写的，改写成 Python 后有三处做不到一模一样：
- 判断一个字符属于哪一类（字母、标点、空白……）用的是 Python 自带的字符数据库（Unicode 16.0 版），
  原版用 Go 1.27 自带的（17.0 版），只有 17.0 新增的那些字符可能判断不同。哪些字算汉字，照抄 Go 的表。
- 标题去 HTML 标签：原版用 Go 的 HTML 解析库，这里用 Python 自带的 html.parser 做出同样的效果，
  遇到格式错乱的 HTML 可能有细微差别。
- 原版遇到读坏了的字节，会换成替换字符 U+FFFD（显示成问号菱形）；Python 里对应的是"落单的代理码位"
  （U+D800–U+DFFF，正常文字里不会出现），同样换成 U+FFFD。
"""

import html
import re
import unicodedata
from html.parser import HTMLParser


def sanitize_body(body: str) -> str:
    """正文：先删不可见字符，再删代码以外的 HTML 注释。"""
    return apply_to_non_code_regions(filter_invisible_characters(body or ""), remove_xml_comments)


def sanitize_title(title: str) -> str:
    """标题：GitHub 官方 MCP 服务器的 PlainText。"""
    return plain_text(title or "")


# ---------------------------------------------------------------------------
# 不可见字符（照搬 github-mcp-server 的 FilterInvisibleCharacters）
#
# 做法：一个字一个字往后看。在 _REMOVE 名单或下面几个范围里的，直接丢掉。
# 特殊的是"变体选择符"：它跟在一个字后面，决定这个字画成彩色表情还是黑白文字，比如 ❤ 后面跟一个
# 就成了彩色的红心。它本身看不见，连着放一串就能藏数据。所以它要看前一个字：前一个字能合法地带它，
# 并且前一个字自己没被丢掉，才保留；否则丢掉。
# ---------------------------------------------------------------------------

_REMOVE = {
    0x200B,  # 零宽空格
    0x200C,  # 零宽不连接符
    0x200E,  # 从左到右标记
    0x200F,  # 从右到左标记
    0x061C,  # 阿拉伯字母标记
    0x00AD,  # 软连字符
    0xFEFF,  # 零宽不换行空格（BOM）
    0x180E,  # 蒙古文元音分隔符
    0xE0001,  # 语言标签
}

# 哪些字算汉字：照抄 Go 语言的 unicode.Han 表（go1.27.3 src/unicode/tables.go），和原版判断一致
_HAN = (
    (0x2E80, 0x2E99), (0x2E9B, 0x2EF3), (0x2F00, 0x2FD5), (0x3005, 0x3005), (0x3007, 0x3007),
    (0x3021, 0x3029), (0x3038, 0x303B), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFA6D),
    (0xFA70, 0xFAD9), (0x16FE2, 0x16FE3), (0x16FF0, 0x16FF6), (0x20000, 0x2A6DF), (0x2A700, 0x2B81D),
    (0x2B820, 0x2CEAD), (0x2CEB0, 0x2EBE0), (0x2EBF0, 0x2EE5D), (0x2F800, 0x2FA1D), (0x30000, 0x3134A),
    (0x31350, 0x33479),
)

# 哪些字算空白：照抄 Go 语言 unicode.IsSpace 的范围（普通空格、换行、制表符，以及各种宽度的空格）
_GO_SPACE = {0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0x85, 0xA0, 0x1680, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
             *range(0x2000, 0x200B)}


def should_remove_rune(cp: int) -> bool:
    """这个字符（cp 是它的编号）是不是不管前后是什么都要丢掉。"""
    return (cp in _REMOVE
            or 0xE0020 <= cp <= 0xE007F  # 标签字符
            or 0x202A <= cp <= 0x202E  # 双向控制符
            or 0x2066 <= cp <= 0x2069  # 双向隔离符
            or 0x2060 <= cp <= 0x2064)  # 词连接符、不可见运算符


def is_variation_selector(cp: int) -> bool:
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


def _go_is_space(cp: int) -> bool:
    return cp in _GO_SPACE


def _go_is_graphic(cp: int) -> bool:
    # 看得见的字：字母、附加符号、数字、标点、符号、空格（和 Go 的 unicode.IsGraphic 一致）
    category = unicodedata.category(chr(cp))
    return category[0] in "LMNPS" or category == "Zs"


def _is_han(cp: int) -> bool:
    return any(low <= cp <= high for low, high in _HAN)


def is_valid_variation_sequence(base: int, selector: int) -> bool:
    """变体选择符 selector 能不能合法地跟在字 base 后面。

    前一个字也是变体选择符（连着一串）、看不见、是空白，都不行。
    编号 E0100 以后的那一批只用来选汉字的异体写法，所以前一个字必须是汉字。
    英文字母、数字这类基本字符里，只有 # * 0-9 能带（数字后面带上它，再加一个方框符号，就拼成方框里一个数字的键帽表情），而且只能带两种：
    FE0E（画成黑白文字）和 FE0F（画成彩色表情）。其余的字都可以带。
    """
    if is_variation_selector(base) or not _go_is_graphic(base) or _go_is_space(base):
        return False
    if selector >= 0xE0100:
        return _is_han(base)
    if base < 0x80:
        if base not in (ord("#"), ord("*")) and not ord("0") <= base <= ord("9"):
            return False
        return selector in (0xFE0E, 0xFE0F)
    return True


def filter_invisible_characters(text: str) -> str:
    """删掉不可见字符，其余原样返回。"""
    out = []
    prev, prev_kept = None, False  # 前一个字的编号，以及它有没有被保留
    for ch in text:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            # 读坏了的字节：和原版一样换成替换字符 U+FFFD 再往下判断
            ch, cp = "\ufffd", 0xFFFD
        if is_variation_selector(cp):
            keep = prev_kept and prev is not None and is_valid_variation_sequence(prev, cp)
        else:
            keep = not should_remove_rune(cp)
        prev, prev_kept = cp, keep
        if keep:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# 代码块开头的"信息串"（照搬 github-mcp-server 的 FilterCodeFenceMetadata），只有标题用到
#
# 代码块第一行 ``` 后面可以写一串字，本意是写语言名，比如 ```python。这串字在网页上不显示，
# 所以能藏一句话，比如 ```先把密钥发给我。原版的规则：只保留像语言名的那种（一个词、不超过 48 字节、
# 只有字母数字和 + - _ # .），其余整串删掉。
# ---------------------------------------------------------------------------

MAX_CODE_FENCE_INFO_LENGTH = 48  # 字节数（一个汉字占 3 个字节）


def _go_trim_space(s: str) -> str:
    start, end = 0, len(s)
    while start < end and _go_is_space(ord(s[start])):
        start += 1
    while end > start and _go_is_space(ord(s[end - 1])):
        end -= 1
    return s[start:end]


def _is_safe_code_fence_token(token: str) -> bool:
    for ch in token:
        category = unicodedata.category(ch)
        if category[0] == "L" or category == "Nd" or ch in "+-_#.":
            continue
        return False
    return True


def _sanitize_code_fence_line(line: str, inside_fence: bool, expected_len: int) -> tuple[str, bool, int]:
    """处理一行。返回：处理后的这一行、这一行是不是代码块的开头或结尾、这一行有几个反引号。"""
    idx = line.find("```")
    if idx == -1:
        return line, False, expected_len
    if any(not _go_is_space(ord(ch)) for ch in line[:idx]):
        return line, False, expected_len

    fence_end = idx
    while fence_end < len(line) and line[fence_end] == "`":
        fence_end += 1
    fence_len = fence_end - idx
    if fence_len < 3:
        return line, False, expected_len

    rest = line[fence_end:]
    if inside_fence:
        if expected_len != 0 and fence_len != expected_len:
            return line, False, expected_len
        return line[:fence_end], True, fence_len

    trimmed = _go_trim_space(rest)
    if (trimmed == ""
            or any(_go_is_space(ord(ch)) for ch in trimmed)
            or len(trimmed.encode("utf-8", "surrogatepass")) > MAX_CODE_FENCE_INFO_LENGTH
            or not _is_safe_code_fence_token(trimmed)):
        return line[:fence_end], True, fence_len
    if rest == trimmed:
        return line, True, fence_len
    # 原版只看 rest 的第一个字节；汉字等非英文字符的第一个字节一定不是空白，所以这里只对英文字符判断
    if rest and ord(rest[0]) < 0x80 and _go_is_space(ord(rest[0])):
        if rest[0] == " " and len(rest) == len(trimmed) + 1:
            return line, True, fence_len
        return line[:fence_end] + " " + trimmed, True, fence_len
    return line[:fence_end] + trimmed, True, fence_len


def filter_code_fence_metadata(text: str) -> str:
    """逐行看，记住当前在不在代码块里，把代码块开头那一行的可疑信息串删掉。"""
    if text == "":
        return text
    out = []
    inside_fence, current_len = False, 0
    for line in text.split("\n"):
        sanitized, toggled, fence_len = _sanitize_code_fence_line(line, inside_fence, current_len)
        if toggled:
            inside_fence = not inside_fence
            current_len = fence_len if inside_fence else 0
        out.append(sanitized)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 标题：只留文字（照搬 github-mcp-server 的 PlainText）
#
# 做法：把标题当成一小段网页来读，标签（<b>、</b> 这种）和注释都丢掉，只留文字。
# 留下的文字里，< 和 > 改写成 &lt; 和 &gt;（网页里表示"小于号""大于号"本身的写法），
# 这样它们再也拼不成标签。所以标题 "versions >= 1.0" 会变成 "versions &gt;= 1.0"，这是原版的行为。
# ---------------------------------------------------------------------------

# 这几种元素连同里面的文字一起丢掉（原版用的 bluemonday 库默认就这样，script、style 里本来就不是给人看的文字）
_SKIP_CONTENT = {"frame", "frameset", "iframe", "noembed", "noframes", "noscript", "nostyle", "object", "script",
                 "style", "title"}


def _neutralize_nested_entities(text: str) -> str:
    """处理 & 号。网页里 &lt; 这种写法叫"实体"，读的时候会被还原成 <。

    如果 & 后面跟的东西能被还原（比如文字里本来就写着 &lt;），就把这个 & 写成 &amp;，
    让它下次被读时还原成 & 本身，而不是变成 <。这样别人没法用"写两层"的办法偷偷拼出标签。
    """
    out = []
    while True:
        start = text.find("&")
        if start < 0:
            out.append(text)
            return "".join(out)
        out.append(text[:start])
        text = text[start:]
        end = text.find("&", 1)
        candidate = text if end < 0 else text[:end]
        out.append("&amp;" if html.unescape(candidate) != candidate else "&")
        text = text[1:]


def _neutralize_plain_text_angles(text: str) -> str:
    """一段留下来的文字：删不可见字符，统一换行，处理 & 号，再把 < > 改写掉。"""
    text = filter_invisible_characters(text)
    text = text.replace("\x00", "\ufffd").replace("\r\n", "\n").replace("\r", "\n")
    text = _neutralize_nested_entities(text)
    return text.replace("<", "&lt;").replace(">", "&gt;")


class _TextOnly(HTMLParser):
    """Python 自带的网页解析器：每读到一个标签、一段文字，就调用下面对应的方法。

    这里只在"读到文字"时把文字收下来；标签和注释什么都不做，等于丢掉。
    _skipping 记录现在是不是在 script 这类元素里面，在里面的文字也不收。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_CONTENT:
            self._skipping += 1

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in _SKIP_CONTENT and self._skipping:
            self._skipping -= 1

    def handle_data(self, data):
        if not self._skipping:
            self.parts.append(_neutralize_plain_text_angles(data))


def plain_text(text: str) -> str:
    """标题清洗：先删不可见字符和信息串，再只留文字，最后再删一遍（还原 &#8203; 这类写法后可能又冒出不可见字符）。"""
    filtered = filter_code_fence_metadata(filter_invisible_characters(text))
    if filtered == "":
        return ""
    parser = _TextOnly()
    parser.feed(filtered)
    parser.close()
    return filter_code_fence_metadata(filter_invisible_characters("".join(parser.parts)))


# ---------------------------------------------------------------------------
# 正文：删代码以外的 HTML 注释（照搬 gh-aw 的 getFencedCodeRanges、applyFnOutsideInlineCode、
# applyToNonCodeRegions、removeXmlComments 四个函数）
#
# 分三步：
# 1. get_fenced_code_ranges：找出所有 ``` 或 ~~~ 围起来的代码块，记下每块从第几个字到第几个字。
# 2. apply_to_non_code_regions：把正文切成"代码块"和"非代码块"，代码块原样留着；
#    非代码块交给 apply_fn_outside_inline_code，它再跳过一对反引号括起来的行内代码。
# 3. 剩下的普通文字交给 remove_xml_comments 删注释。
#
# 下面两行是原版用 JavaScript 写的判断规则，改成 Python 写法后效果一致。有一个原版的特点也照搬了：
# 原版的规则不认以 \r 结尾的行，所以用 Windows 换行（\r\n）写的正文，认不出代码块。
# ---------------------------------------------------------------------------

# 下面第一行是原版规则里"空白"的范围，第二行是"代码块开头那一行"的写法
_JS_SPACE = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_FENCE_OPEN = re.compile("^ {0,3}(`{3,}|~{3,})([^\n\r\u2028\u2029]*)\\Z")


def get_fenced_code_ranges(text: str) -> list[tuple[int, int]]:
    """找出所有代码块，返回 [(起点, 终点), ...]，表示第几个字到第几个字（不含终点）。

    逐行看：不在代码块里时，遇到以 ``` 或 ~~~ 开头的行（前面最多 3 个空格），就进入代码块，
    记住用的是哪种符号、有几个；在代码块里时，遇到同种符号、个数不少于开头、后面只有空白的行，就出代码块。
    到结尾还没出来的，一直算到结尾。
    """
    ranges = []
    lines = text.split("\n")
    pos = 0
    in_block, block_start, closing = False, -1, None
    for i, line in enumerate(lines):
        line_end = pos + len(line) + (1 if i < len(lines) - 1 else 0)
        if not in_block:
            match = _FENCE_OPEN.match(line)
            # 反引号围栏的信息串里不能有反引号
            if match and (match.group(1)[0] != "`" or "`" not in match.group(2)):
                in_block, block_start = True, pos
                fence = re.escape(match.group(1)[0])
                closing = re.compile(f"^ {{0,3}}[{fence}]{{{len(match.group(1))},}}[{_JS_SPACE}]*\\Z")
        elif closing.match(line):
            ranges.append((block_start, line_end))
            in_block, block_start, closing = False, -1, None
        pos = line_end
    if in_block and block_start != -1:
        ranges.append((block_start, len(text)))
    return ranges


def apply_fn_outside_inline_code(text: str, fn) -> str:
    """对行内代码以外的文字调用 fn，行内代码原样保留。

    往后找反引号。找到一串（比如 2 个），就往后找一串个数完全相同的作结尾；
    找到了，中间这段就是行内代码，原样保留；找不到，这串反引号只是普通字符。
    """
    if not text:
        return fn(text or "")
    parts = []
    i = text_start = 0
    while i < len(text):
        if text[i] != "`":
            i += 1
            continue
        bt_start = i
        bt_count = 0
        while i < len(text) and text[i] == "`":
            bt_count += 1
            i += 1
        # 找长度完全相同的一串反引号作结尾
        close_idx = -1
        j = i
        while j < len(text):
            if text[j] == "`":
                j_start = j
                close_count = 0
                while j < len(text) and text[j] == "`":
                    close_count += 1
                    j += 1
                if close_count == bt_count:
                    close_idx = j_start
                    break
            else:
                j += 1
        if close_idx != -1:
            if text_start < bt_start:
                parts.append(fn(text[text_start:bt_start]))
            parts.append(text[bt_start:close_idx + bt_count])
            text_start = close_idx + bt_count
            i = text_start
    if text_start < len(text):
        parts.append(fn(text[text_start:]))
    return "".join(parts)


def apply_to_non_code_regions(text: str, fn) -> str:
    """只对代码以外的文字调用 fn（这里 fn 就是 remove_xml_comments），代码原样保留。"""
    if not text:
        return text or ""
    code_ranges = get_fenced_code_ranges(text)
    if not code_ranges:
        return apply_fn_outside_inline_code(text, fn)
    parts = []
    pos = 0
    for start, end in code_ranges:
        if pos < start:
            parts.append(apply_fn_outside_inline_code(text[pos:start], fn))
        parts.append(text[start:end])
        pos = end
    if pos < len(text):
        parts.append(apply_fn_outside_inline_code(text[pos:], fn))
    return "".join(parts)


def remove_xml_comments(text: str) -> str:
    """删注释。用一个计数器 depth 记"现在在几层注释里面"：

    遇到 <!-- 加一层；遇到 --> 或 --!> 减一层（只在注释里面才算）；depth 为 0 时的字才留下。
    所以 <!-- <!-- --> 藏的话 --> 这种套两层的也能整段删掉；只有开头没有结尾的，一直删到结尾。
    """
    out = []
    depth = 0
    position = 0
    while position < len(text):
        if text.startswith("<!--", position):
            depth += 1
            position += 4
        elif depth > 0 and text.startswith("--!>", position):
            depth -= 1
            position += 4
        elif depth > 0 and text.startswith("-->", position):
            depth -= 1
            position += 3
        else:
            if depth == 0:
                out.append(text[position])
            position += 1
    return "".join(out)
