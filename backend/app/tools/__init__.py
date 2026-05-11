import json
from . import search_memory as _search
from . import save_to_graph as _save
from . import read_file as _read
from . import write_file as _write
from . import edit_file as _edit
from . import search_files as _glob
from . import search_content as _grep
from . import run_command as _bash
from . import delete_from_graph as _delete

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "搜索知识图谱中的长期记忆。当用户提到之前讨论过的话题、人物、事件或概念时，先调用此工具检索。每轮对话必须调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然语言搜索查询，描述你想查找什么",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量，默认5",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_to_graph",
            "description": "将重要信息保存到知识图谱。当用户表达偏好、事实、经历、计划等值得记住的内容时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "array",
                        "description": "新增或更新的实体节点",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "properties": {"type": "object"},
                            },
                            "required": ["name", "type"],
                        },
                    },
                    "relations": {
                        "type": "array",
                        "description": "实体之间的关系",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from_type": {"type": "string"},
                                "from_name": {"type": "string"},
                                "to_type": {"type": "string"},
                                "to_name": {"type": "string"},
                                "rel_type": {"type": "string"},
                                "properties": {"type": "object"},
                            },
                            "required": ["from_type", "from_name", "to_type", "to_name", "rel_type"],
                        },
                    },
                    "facts": {
                        "type": "array",
                        "description": "要记住的事实",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "type": {"type": "string"},
                                "about_entities": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["content", "type"],
                        },
                    },
                    "importance": {
                        "type": "integer",
                        "description": "手动设置实体重要度 1-10。常用记忆设高分，临时信息设低分。不传则自动 +1。",
                    },
                    "pinned": {
                        "type": "boolean",
                        "description": "是否固定注入到每次对话。设为 true 时重要度自动变为 10。",
                    },
                },
                "required": ["nodes", "relations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_from_graph",
            "description": "从知识图谱中删除记忆。用于删除不相关、错误或测试用的实体/事实/关系。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_type": {
                        "type": "string",
                        "description": "删除目标类型: entity(实体,按名称), fact(事实,按ID), fact_by_content(按内容关键词匹配), relation(关系,格式 from_name||to_name)",
                    },
                    "target": {
                        "type": "string",
                        "description": "删除目标: entity填名称, fact填数字ID, fact_by_content填关键词, relation填 from_name||to_name",
                    },
                    "rel_type": {
                        "type": "string",
                        "description": "仅 relation 类型时可选，精确匹配关系类型",
                    },
                },
                "required": ["target_type", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memory",
            "description": "列出知识图谱中所有实体和事实，用于浏览查找要删除的条目。",
            "parameters": {
                "type": "object",
                "properties": {
                    "otype": {
                        "type": "string",
                        "description": "列出类型: entities, facts, all(默认)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容，支持分页（offset/limit）。先搜索再读取，不要在未知文件时猜测路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件的绝对路径，或相对于项目根目录的路径",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（从1开始），默认1",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大行数，默认200",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖写入文件。会自动创建父目录。请确保路径在白名单范围内。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件的绝对路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文件内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "精确字符串替换编辑文件。old_string 必须在文件中唯一匹配，否则需提供更多上下文或设置 replace_all=true。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要编辑的文件路径",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "要被替换的原文本（必须精确匹配）",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "替换后的新文本",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "是否替换所有匹配项，默认 false（仅替换第一处）",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "按 glob 模式搜索文件名。如 '**/*.py' 找所有 Python 文件，'src/**/*.vue' 找 Vue 组件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "glob 匹配模式，如 '**/*.py'",
                    },
                    "directory": {
                        "type": "string",
                        "description": "搜索目录，默认为项目根目录",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_content",
            "description": "使用正则表达式搜索文件内容。返回 文件:行号:内容 格式的结果。修改文件前先用此工具查找相关代码。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "正则表达式搜索模式",
                    },
                    "directory": {
                        "type": "string",
                        "description": "搜索目录，默认为项目根目录",
                    },
                    "file_types": {
                        "type": "string",
                        "description": "逗号分隔的文件扩展名过滤，如 '.py,.ts,.vue'",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在项目目录中执行命令。用于运行构建、测试、lint、安装依赖等。命令超时60秒，输出上限8000字符。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的命令行",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "工作目录，必须在白名单内，默认项目根目录",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认60",
                    },
                },
                "required": ["command"],
            },
        },
    },
]


async def dispatch(name: str, args: dict, conv_id: str | None = None) -> str:
    try:
        if name == "search_memory":
            return await _search.search_memory(
                query=args["query"],
                top_k=args.get("top_k", 5),
            )
        elif name == "save_to_graph":
            return await _save.save_to_graph(
                nodes=args.get("nodes", []),
                relations=args.get("relations", []),
                facts=args.get("facts"),
                conv_id=conv_id,
                importance=args.get("importance"),
                pinned=args.get("pinned"),
            )
        elif name == "read_file":
            return await _read.read_file(
                path=args["path"],
                offset=args.get("offset", 1),
                limit=args.get("limit", 200),
            )
        elif name == "write_file":
            return await _write.write_file(
                path=args["path"],
                content=args["content"],
            )
        elif name == "edit_file":
            return await _edit.edit_file(
                path=args["path"],
                old_string=args["old_string"],
                new_string=args["new_string"],
                replace_all=args.get("replace_all", False),
            )
        elif name == "search_files":
            return await _glob.search_files(
                pattern=args["pattern"],
                directory=args.get("directory"),
            )
        elif name == "search_content":
            return await _grep.search_content(
                pattern=args["pattern"],
                directory=args.get("directory"),
                file_types=args.get("file_types"),
            )
        elif name == "run_command":
            return await _bash.run_command(
                command=args["command"],
                workdir=args.get("workdir"),
                timeout=args.get("timeout", 60),
            )
        elif name == "delete_from_graph":
            return await _delete.delete_from_graph(
                target_type=args["target_type"],
                target=args["target"],
                rel_type=args.get("rel_type"),
            )
        elif name == "list_memory":
            return await _delete.list_memory(
                otype=args.get("otype", "all"),
            )
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool error ({name}): {e}"
