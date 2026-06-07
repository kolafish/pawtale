const DEFAULT_SESSION_TITLE = "回忆录访谈";

const DEFAULT_MEMOIR_QUESTIONS = Object.freeze([
  "你小时候最常去的一个地方是哪里？那里有什么声音、气味，或者你现在还记得的细节？",
  "你最早清楚记得的人是谁？当时你们之间发生了什么？",
  "有没有一件事让你觉得自己长大了，或者开始用不同的方式看待世界？",
  "你生命里有没有一次重要的选择？当时你为什么那样决定？",
  "现在回头看，你最想把哪段经历留给家人或未来的自己？",
]);

const DEFAULT_COVERAGE_TOPICS = Object.freeze([
  {
    key: "childhood",
    name: "童年",
    life_stage: "童年",
    description: "童年居住环境、玩伴、日常和早期记忆",
    keywords: ["童年", "小时候", "小时", "玩伴", "外婆", "爷爷", "奶奶"],
  },
  {
    key: "school",
    name: "上学",
    life_stage: "少年",
    description: "求学经历、老师同学、学习压力和关键选择",
    keywords: ["上学", "学校", "老师", "同学", "读书", "考试"],
  },
  {
    key: "family_origin",
    name: "父母家庭",
    life_stage: "童年",
    description: "父母、兄弟姐妹、家庭氛围和成长影响",
    keywords: ["父亲", "母亲", "爸爸", "妈妈", "兄弟", "姐妹", "家里"],
  },
  {
    key: "first_job",
    name: "第一次工作",
    life_stage: "青年",
    description: "第一次工作、离乡、职业起点和早期社会经验",
    keywords: ["第一次工作", "上班", "工作", "厂", "单位", "离乡", "重庆"],
  },
  {
    key: "marriage",
    name: "婚姻",
    life_stage: "成年",
    description: "恋爱、婚姻、伴侣关系和家庭组建",
    keywords: ["结婚", "婚姻", "恋爱", "对象", "伴侣", "爱人"],
  },
  {
    key: "children",
    name: "孩子",
    life_stage: "成年",
    description: "养育孩子、亲子关系和家庭责任",
    keywords: ["孩子", "儿子", "女儿", "带娃", "养育", "子女"],
  },
  {
    key: "turning_points",
    name: "重大转折",
    life_stage: null,
    description: "改变人生方向的重要事件和选择",
    keywords: ["转折", "决定", "选择", "改变", "后来", "离开"],
  },
  {
    key: "regret",
    name: "遗憾",
    life_stage: null,
    description: "尚未释怀、想重新理解或需要温和处理的经历",
    keywords: ["遗憾", "后悔", "可惜", "如果", "没能"],
  },
  {
    key: "pride",
    name: "骄傲",
    life_stage: null,
    description: "成就、坚持、价值感和希望被记住的部分",
    keywords: ["骄傲", "自豪", "成就", "坚持", "值得"],
  },
  {
    key: "later_life",
    name: "晚年生活",
    life_stage: "晚年",
    description: "当下生活、身体状态、关系和回望人生的视角",
    keywords: ["现在", "晚年", "退休", "身体", "生活", "回头看"],
  },
]);

module.exports = {
  DEFAULT_COVERAGE_TOPICS,
  DEFAULT_MEMOIR_QUESTIONS,
  DEFAULT_SESSION_TITLE,
};
