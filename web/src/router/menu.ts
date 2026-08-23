/**
 * 侧边栏菜单配置
 */
export interface MenuItem {
  path: string;
  title: string;
  icon: string;
}

export const menuItems: MenuItem[] = [
  { path: "/home",      title: "首页",       icon: "HomeFilled" },
  { path: "/chat",      title: "智能问答",    icon: "ChatDotRound" },
  { path: "/papers",    title: "论文助手",    icon: "Reading" },
  { path: "/settings",  title: "系统设置",    icon: "Setting" },
];
