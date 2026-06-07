from dataclasses import dataclass


@dataclass(frozen=True)
class DefaultCoverageTopic:
    key: str
    name: str
    life_stage: str | None
    description: str
    keywords: tuple[str, ...]


DEFAULT_COVERAGE_TOPICS: tuple[DefaultCoverageTopic, ...] = (
    DefaultCoverageTopic("childhood", "童年", "童年", "童年居住环境、玩伴、日常和早期记忆", ("童年", "小时候", "小时", "玩伴", "外婆", "爷爷", "奶奶")),
    DefaultCoverageTopic("school", "上学", "少年", "求学经历、老师同学、学习压力和关键选择", ("上学", "学校", "老师", "同学", "读书", "考试")),
    DefaultCoverageTopic("family_origin", "父母家庭", "童年", "父母、兄弟姐妹、家庭氛围和成长影响", ("父亲", "母亲", "爸爸", "妈妈", "兄弟", "姐妹", "家里")),
    DefaultCoverageTopic("first_job", "第一次工作", "青年", "第一次工作、离乡、职业起点和早期社会经验", ("第一次工作", "上班", "工作", "厂", "单位", "离乡", "重庆")),
    DefaultCoverageTopic("marriage", "婚姻", "成年", "恋爱、婚姻、伴侣关系和家庭组建", ("结婚", "婚姻", "恋爱", "对象", "伴侣", "爱人")),
    DefaultCoverageTopic("children", "孩子", "成年", "养育孩子、亲子关系和家庭责任", ("孩子", "儿子", "女儿", "带娃", "养育", "子女")),
    DefaultCoverageTopic("turning_points", "重大转折", None, "改变人生方向的重要事件和选择", ("转折", "决定", "选择", "改变", "后来", "离开")),
    DefaultCoverageTopic("regret", "遗憾", None, "尚未释怀、想重新理解或需要温和处理的经历", ("遗憾", "后悔", "可惜", "如果", "没能")),
    DefaultCoverageTopic("pride", "骄傲", None, "成就、坚持、价值感和希望被记住的部分", ("骄傲", "自豪", "成就", "坚持", "值得")),
    DefaultCoverageTopic("later_life", "晚年生活", "晚年", "当下生活、身体状态、关系和回望人生的视角", ("现在", "晚年", "退休", "身体", "生活", "回头看")),
)
