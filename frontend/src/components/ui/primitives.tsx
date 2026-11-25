/*! @license
MIT License

Copyright (c) 2023 shadcn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
*/
/**
 * Adapted from shadcn/ui components published on 21st.dev.
 * Copyright (c) 2023 shadcn. MIT; see LICENSES/shadcn-ui.txt.
 * Tailwind, cva and unused Radix composition are replaced by local CSS/native elements.
 */
import type { ComponentProps } from 'react';

const classes = (base: string, className?: string) => className ? `${base} ${className}` : base;

export type ButtonProps = ComponentProps<'button'> & {
  variant?: 'default' | 'secondary' | 'outline' | 'ghost' | 'destructive' | 'link';
  size?: 'default' | 'sm' | 'lg' | 'icon';
};

export function Button({ className, variant = 'default', size = 'default', type, ...props }: ButtonProps) {
  return <button data-slot="button" data-variant={variant} data-size={size} className={classes('ui-button', className)} type={type} {...props} />;
}

export type BadgeProps = ComponentProps<'span'> & {
  variant?: 'default' | 'secondary' | 'outline' | 'success' | 'warning' | 'danger' | 'destructive' | 'info' | 'neutral';
  dot?: boolean;
};

export function Badge({ className, variant = 'default', dot = false, children, ...props }: BadgeProps) {
  return <span data-slot="badge" data-variant={variant} className={classes('ui-badge', className)} {...props}>{dot && <span className="ui-badge-dot" aria-hidden="true" />}{children}</span>;
}

export function Card({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="card" className={classes('ui-card', className)} {...props} />;
}

export function CardHeader({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="card-header" className={classes('ui-card-header', className)} {...props} />;
}

export function CardTitle({ className, ...props }: ComponentProps<'h3'>) {
  return <h3 data-slot="card-title" className={classes('ui-card-title', className)} {...props} />;
}

export function CardDescription({ className, ...props }: ComponentProps<'p'>) {
  return <p data-slot="card-description" className={classes('ui-card-description', className)} {...props} />;
}

export function CardAction({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="card-action" className={classes('ui-card-action', className)} {...props} />;
}

export function CardContent({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="card-content" className={classes('ui-card-content', className)} {...props} />;
}

export function CardFooter({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="card-footer" className={classes('ui-card-footer', className)} {...props} />;
}

export function Empty({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="empty" className={classes('ui-empty', className)} {...props} />;
}

export function EmptyHeader({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="empty-header" className={classes('ui-empty-header', className)} {...props} />;
}

export function EmptyMedia({ className, variant = 'default', ...props }: ComponentProps<'div'> & { variant?: 'default' | 'icon' }) {
  return <div data-slot="empty-media" data-variant={variant} className={classes('ui-empty-media', className)} {...props} />;
}

export function EmptyTitle({ className, ...props }: ComponentProps<'h3'>) {
  return <h3 data-slot="empty-title" className={classes('ui-empty-title', className)} {...props} />;
}

export function EmptyDescription({ className, ...props }: ComponentProps<'p'>) {
  return <p data-slot="empty-description" className={classes('ui-empty-description', className)} {...props} />;
}

export function EmptyContent({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="empty-content" className={classes('ui-empty-content', className)} {...props} />;
}

export function Sidebar({ className, ...props }: ComponentProps<'aside'>) {
  return <aside data-slot="sidebar" className={classes('ui-sidebar', className)} {...props} />;
}

export function SidebarHeader({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="sidebar-header" className={classes('ui-sidebar-header', className)} {...props} />;
}

export function SidebarContent({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="sidebar-content" className={classes('ui-sidebar-content', className)} {...props} />;
}

export function SidebarGroup({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="sidebar-group" className={classes('ui-sidebar-group', className)} {...props} />;
}

export function SidebarGroupLabel({ className, ...props }: ComponentProps<'p'>) {
  return <p data-slot="sidebar-group-label" className={classes('ui-sidebar-group-label', className)} {...props} />;
}

export function SidebarGroupContent({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="sidebar-group-content" className={classes('ui-sidebar-group-content', className)} {...props} />;
}

export function SidebarMenu({ className, ...props }: ComponentProps<'ul'>) {
  return <ul data-slot="sidebar-menu" className={classes('ui-sidebar-menu', className)} {...props} />;
}

export function SidebarMenuItem({ className, ...props }: ComponentProps<'li'>) {
  return <li data-slot="sidebar-menu-item" className={classes('ui-sidebar-menu-item', className)} {...props} />;
}

export function SidebarMenuButton({ className, isActive = false, type = 'button', ...props }: ComponentProps<'button'> & { isActive?: boolean }) {
  return <button data-slot="sidebar-menu-button" data-active={isActive} aria-current={isActive ? 'page' : undefined} className={classes('ui-sidebar-menu-button', className)} type={type} {...props} />;
}

export function SidebarFooter({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="sidebar-footer" className={classes('ui-sidebar-footer', className)} {...props} />;
}
