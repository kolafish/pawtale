const { handleMemoirApiRequest } = require("./src/router");

exports.main = async (event, context) => handleMemoirApiRequest(event, context);
