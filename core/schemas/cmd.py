class BaseCommand:
    """简洁的命令类"""

    def __init__(self, main_cmd: str = "", sub_cmd: str = "", **kwargs):
        """
        初始化命令

        Args:
            main_cmd: 主命令，如 "show"
            sub_cmd: 辅命令，如 "scheduler"
            **kwargs: 其他参数，如 verbose=True, limit=10
        """
        self.main_cmd = main_cmd.lower() if main_cmd else ""
        self.sub_cmd = sub_cmd.lower() if sub_cmd else ""
        self.params = kwargs or {}

    @classmethod
    def build_cmd(cls, cmd_str: str) -> 'BaseCommand':
        """
        从字符串构建命令

        Args:
            cmd_str: 命令字符串

        Returns:
            BaseCommand对象
        """
        if not cmd_str or not cmd_str.strip():
            return cls()

        parts = cls._split_cmd(cmd_str.strip())
        if not parts:
            return cls()

        # 提取主命令
        main_cmd = parts[0].lower()

        # 提取辅命令（如果第二部分不是选项）
        sub_cmd = ""
        # 跳过已处理的部分
        processed_parts = 1

        # 检查是否有辅命令（第二个部分不是以-开头）
        if len(parts) > 1 and not parts[1].startswith('-'):
            sub_cmd = parts[1].lower()
            processed_parts = 2

        # 解析参数
        params = {}
        i = processed_parts

        while i < len(parts):
            part = parts[i]

            if part.startswith('--'):
                # 长参数 --name=value 或 --name
                name = part[2:]
                if '=' in name:
                    key, value = name.split('=', 1)
                    params[key] = cls._parse_value(value)
                else:
                    # 如果没有值，视为布尔真值
                    params[name] = True

            elif part.startswith('-'):
                # 短参数 -name value 或 -name
                name = part[1:]
                if i + 1 < len(parts) and not parts[i + 1].startswith('-'):
                    # 下一个是值
                    value = parts[i + 1]
                    params[name] = cls._parse_value(value)
                    i += 1  # 跳过已使用的值
                else:
                    # 没有值，视为布尔真值
                    params[name] = True

            # 注意：这里不再将非选项当作辅命令，因为我们已经处理了辅命令
            # 如果还有非选项，应该是位置参数，但根据我们的设计，位置参数不作为单独字段
            # 可以将它们添加到params中，键名用数字表示，如 _0, _1 等
            elif i == 1 and not sub_cmd:  # 这个情况实际上不会发生，因为上面已经处理了
                sub_cmd = part.lower()
            else:
                # 额外的位置参数，保存到params中
                pos_index = i - processed_parts
                params[f"_{pos_index}"] = part

            i += 1

        return cls(main_cmd, sub_cmd, **params)

    @staticmethod
    def _split_cmd(cmd_str: str) -> list:
        """简单的命令分割，支持引号"""
        parts = []
        current = []
        in_quote = False
        quote_char = None

        for char in cmd_str:
            if char in ('"', "'"):
                if not in_quote:
                    in_quote = True
                    quote_char = char
                elif char == quote_char:
                    in_quote = False
                    quote_char = None
                else:
                    current.append(char)
            elif char.isspace() and not in_quote:
                if current:
                    parts.append(''.join(current))
                    current = []
            else:
                current.append(char)

        if current:
            parts.append(''.join(current))

        return parts

    @staticmethod
    def _parse_value(value: str):
        """解析参数值，尝试自动转换类型"""
        # 尝试转换为整数
        try:
            return int(value)
        except ValueError:
            pass

        # 尝试转换为浮点数
        try:
            return float(value)
        except ValueError:
            pass

        # 尝试转换为布尔值
        lower_val = value.lower()
        if lower_val in ('true', 'yes', 'on'):
            return True
        elif lower_val in ('false', 'no', 'off'):
            return False

        # 保持为字符串
        return value

    def is_equivalent(self, other: 'BaseCommand', compare_params: bool = False) -> bool:
        """
        判断两个命令是否等价

        Args:
            other: 另一个BaseCommand对象
            compare_params: 是否比较参数，默认为False（只比较主命令和辅命令）

        Returns:
            如果两个命令等价返回True，否则False
        """
        if not isinstance(other, BaseCommand):
            return False

        # 比较主命令和辅命令
        if self.main_cmd != other.main_cmd:
            return False

        if self.sub_cmd != other.sub_cmd:
            return False

        # 如果需要比较参数
        if compare_params:
            # 比较参数个数
            if len(self.params) != len(other.params):
                return False

            # 比较每个参数
            for key, value in self.params.items():
                if key not in other.params:
                    return False

                # 值比较
                if isinstance(value, (int, float, bool)):
                    # 数值和布尔值严格比较
                    if value != other.params[key]:
                        return False
                else:
                    # 字符串比较（忽略大小写）
                    if str(value).lower() != str(other.params[key]).lower():
                        return False

        return True

    def __eq__(self, other) -> bool:
        """重载==运算符，默认比较主命令和辅命令，不比较参数"""
        return self.is_equivalent(other, compare_params=False)

    def __str__(self) -> str:
        """转换为字符串表示"""
        result = self.main_cmd
        if self.sub_cmd:
            result += f" {self.sub_cmd}"

        for key, value in self.params.items():
            # 跳过位置参数的特殊表示
            if key.startswith('_'):
                result += f" {value}"
            elif isinstance(value, bool) and value is True:
                # 布尔真值简化为--name
                result += f" --{key}"
            elif isinstance(value, bool) and value is False:
                # 布尔假值不显示（或显示为--no-name，这里简单处理）
                pass
            elif len(key) == 1:
                # 单字符参数用-前缀
                result += f" -{key} {value}"
            else:
                # 多字符参数用--前缀
                result += f" --{key}={value}"

        return result

    def __repr__(self) -> str:
        return f"BaseCommand('{self.main_cmd}', '{self.sub_cmd}', {self.params})"

    def __contains__(self, key: str) -> bool:
        """检查参数是否存在"""
        return key in self.params

    def get(self, key: str, default=None):
        """获取参数值，不存在时返回默认值"""
        return self.params.get(key, default)

    def set(self, key: str, value):
        """设置参数值"""
        self.params[key] = value

    def matches(self, main_cmd: str, sub_cmd: str = "") -> bool:
        """简化方法：检查是否匹配指定的主命令和辅命令"""
        return (self.main_cmd == main_cmd.lower() and
                self.sub_cmd == sub_cmd.lower())


# 测试和使用示例
if __name__ == "__main__":
    # 测试空字符串
    print("测试空字符串:")
    cmd1 = BaseCommand.build_cmd("")
    print(f"空命令: {cmd1}")

    # 测试回车/空白字符串
    cmd2 = BaseCommand.build_cmd("   \n\t  ")
    print(f"空白命令: {cmd2}")
    print(f"是否等价: {cmd1 == cmd2}")

    print("\n" + "=" * 50)

    # 测试各种命令格式
    test_cases = [
        "show",
        "show scheduler",
        "SHOW SCHEDULER",  # 测试大小写
        "show scheduler --verbose",
        "show scheduler --limit=10",
        "show scheduler --format json",
        "show scheduler -v -l 20 --format=json",
        "connect database --host=localhost --port=3306",
        "add item 'test item' --category tech",
    ]

    for test in test_cases:
        print(f"\n输入: {test}")
        cmd = BaseCommand.build_cmd(test)
        print(f"解析结果: {cmd}")
        print(f"  主命令: {cmd.main_cmd}")
        print(f"  辅命令: {cmd.sub_cmd}")
        print(f"  参数: {cmd.params}")

    print("\n" + "=" * 50)

    # 测试命令等价性
    print("\n测试命令等价性:")

    # 创建基础命令（不带参数）
    base_show = BaseCommand("show", "scheduler")
    base_connect = BaseCommand("connect", "database")
    base_add = BaseCommand("add", "item")

    # 创建带参数的命令
    cmd_with_params = BaseCommand.build_cmd("show scheduler --verbose --limit=10")

    print(f"基础命令: {base_show}")
    print(f"带参数命令: {cmd_with_params}")
    print(f"基础命令 == 带参数命令: {base_show == cmd_with_params}")

    # 测试严格等价（比较参数）
    print(f"严格等价 (比较参数): {base_show.is_equivalent(cmd_with_params, compare_params=True)}")  # 应该为False
    print(f"宽松等价 (不比较参数): {base_show.is_equivalent(cmd_with_params, compare_params=False)}")  # 应该为True

    print("\n" + "=" * 50)

    # 使用示例：命令行控制台
    print("模拟命令行控制台:")

    # 定义预期的命令
    expected_show = BaseCommand("show", "scheduler")
    expected_connect = BaseCommand("connect", "database")
    expected_add = BaseCommand("add", "item")

    # 模拟用户输入
    user_inputs = [
        "show scheduler",
        "SHOW SCHEDULER",
        "show scheduler --verbose",
        "show scheduler --limit=10 --format=json",
        "connect database --host=localhost",
        "add item --name=test",
        "invalid command",
    ]

    for user_input in user_inputs:
        user_cmd = BaseCommand.build_cmd(user_input)

        # 使用==比较，忽略参数
        if user_cmd == expected_show:
            print(f"'{user_input}' -> 执行show命令")
            print(f"  参数: {user_cmd.params}")
        elif user_cmd == expected_connect:
            print(f"'{user_input}' -> 执行connect命令")
            print(f"  参数: {user_cmd.params}")
        elif user_cmd == expected_add:
            print(f"'{user_input}' -> 执行add命令")
            print(f"  参数: {user_cmd.params}")
        else:
            print(f"'{user_input}' -> 未知命令")

    print("\n" + "=" * 50)
    print("测试matches方法:")

    # 使用matches方法进行更直观的匹配
    test_cmds = [
        BaseCommand.build_cmd("show scheduler --verbose"),
        BaseCommand.build_cmd("connect database"),
        BaseCommand.build_cmd("add item --name=test"),
        BaseCommand.build_cmd("delete file"),
    ]

    for test_cmd in test_cmds:
        if test_cmd.matches("show", "scheduler"):
            print(f"{test_cmd} -> 匹配 show scheduler")
        elif test_cmd.matches("connect", "database"):
            print(f"{test_cmd} -> 匹配 connect database")
        elif test_cmd.matches("add", "item"):
            print(f"{test_cmd} -> 匹配 add item")
        else:
            print(f"{test_cmd} -> 无匹配")

    print("\n================列表参数")
    cmd = BaseCommand.build_cmd("connect mooncake --list=1,2,3,4,5")  # 逗号不能加空格
    print(cmd.main_cmd)
    print(f"解析结果: {cmd}")
    print(f"  主命令: {cmd.main_cmd}")
    print(f"  辅命令: {cmd.sub_cmd}")
    print(f"  参数: {cmd.params}")  # 暂时只能手动分开了

    print(list(eval(cmd.params.get("list"))))
