const net = require("node:net");

function findAvailablePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        if (!port) {
          reject(new Error("无法为开发者工具分配自动化端口"));
          return;
        }
        resolve(port);
      });
    });
  });
}

module.exports = {
  findAvailablePort,
};
