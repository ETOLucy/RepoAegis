"""agent 执行的命令能看到的环境变量。

bash 和 submit 开子进程时不继承主程序的全部环境变量，而是用 command_env() 生成的这一份。

规则（2026-10-27 定，依据见 docs/调研/agent命令的环境变量.md）：

- 白名单：只从主程序的环境里抄 KEEP 里的变量，其余一概不给（照 AWF 默认、Codex 的 inherit=core）。
  不用黑名单：不该给的变量列不完，漏掉一个就给了 agent。
- 再加上 FIXED 里写死的值：不分页、不上色、不画进度条，编码用 UTF-8。命令没有标准输入，
  输出写进文件且超过 1 万字符就截断，分页器会卡到超时，进度条会把输出撑满。
- 最后检查变量名：含 SUSPICIOUS_WORDS 之一就报错，不开工。白名单里本不该有这种变量，
  出现了说明配置错了，要让人看见，而不是悄悄删掉。

这一层只防 agent 用 env 之类的命令直接看到变量。它挡不住 agent 读主程序启动时的环境
（/proc/<进程号>/environ），所以真密钥本来就不能放在主程序里，模型的 key 由 AWF 的密钥代理拿着。
"""

import os
from collections.abc import Mapping

# 只写名字，值运行时从主程序的环境里抄：代理地址由 AWF 每次启动时定，HOME、PATH 每台机器不同
KEEP = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR",
    # AWF 设的出网代理，不抄命令就连不上网；有的工具认大写，有的认小写
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    # AWF 筛过的证书文件，各工具认各自的变量名：大多数程序、curl、Python requests、git、Node
    # （2026-10-30 加，实测见 docs/调研/AWF沙箱实测.md）
    "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "GIT_SSL_CAINFO",
    "NODE_EXTRA_CA_CERTS",
)

FIXED = {
    "CI": "true",  # 和用户仓库自己的 CI 一致，Actions 的 run 步骤本来就带它
    # 长输出直接打印完，不用 less 这类分页器停下来等人按键
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "MANPAGER": "cat",
    # 不输出颜色控制字符，写进文件就是乱码
    "NO_COLOR": "1",
    "TERM": "dumb",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    # 进度条每刷新一次多一行，很快占满输出上限
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
}

SUSPICIOUS_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")


class SuspiciousEnvError(Exception):
    """要交给 agent 的环境变量里有名字像凭据的。消息里只有名字，没有值。"""


def command_env(source: Mapping[str, str] | None = None,
                extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """生成 agent 命令的全部环境变量。

    source 是从哪里抄 KEEP，默认是本进程的环境；extra 留给以后要加的变量（例如项目虚拟环境的 PATH），
    同名时 extra 优先。
    """
    if source is None:
        source = os.environ
    env = {name: source[name] for name in KEEP if name in source}
    env.update(FIXED)
    env.update(extra or {})
    suspicious = sorted(name for name in env if any(word in name.upper() for word in SUSPICIOUS_WORDS))
    if suspicious:
        raise SuspiciousEnvError(f"these environment variable names look like credentials and must not be "
                                 f"given to the agent: {', '.join(suspicious)}")
    return env
