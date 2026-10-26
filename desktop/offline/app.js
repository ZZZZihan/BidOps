const retry = document.getElementById('retry');
const message = document.getElementById('status-message');
const address = document.getElementById('service-address');
const region = document.getElementById('status-region');
let busy = false;

function render(status) {
  message.textContent = status.message;
  address.textContent = status.address;
  region.dataset.state = status.state;
}

async function connect() {
  if (busy) return;
  busy = true;
  retry.disabled = true;
  retry.textContent = '正在连接…';
  render({state: 'connecting', address: address.textContent, message: '正在连接本机工作台…'});
  try {
    render(await window.mygo.call('Desktop.Connect'));
  } catch {
    message.textContent = '连接未完成，请重新尝试。';
    region.dataset.state = 'offline';
  } finally {
    busy = false;
    retry.disabled = false;
    retry.textContent = '重新连接 ↗';
  }
}

retry.addEventListener('click', () => void connect());

async function initialize() {
  if (!window.mygo || typeof window.mygo.call !== 'function') {
    message.textContent = '请通过 BidOps 桌面应用打开工作台。';
    retry.disabled = true;
    return;
  }
  try {
    const status = await window.mygo.call('Desktop.Status');
    render(status);
    if (status.state === 'idle') await connect();
  } catch {
    message.textContent = '桌面连接暂未就绪，请通过工作台菜单重新载入。';
    retry.disabled = true;
  }
}

void initialize();
