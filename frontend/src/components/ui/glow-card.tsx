/*! @license
MIT License

Copyright (c) 2026 Chánh Đại

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
 * Adapted from Chánh Đại's Glow Card Grid, published on 21st.dev.
 * Copyright (c) 2026 Chánh Đại. MIT; see LICENSES/chanhdai.txt.
 * Generalized content, local/coalesced pointer handling and reduced-motion support.
 */
import { useEffect, useRef } from 'react';
import type { ComponentPropsWithoutRef } from 'react';

export function GlowCard({ className, onPointerMove, onPointerLeave, ...props }: ComponentPropsWithoutRef<'div'>) {
  const cardRef = useRef<HTMLDivElement>(null);
  const enabledRef = useRef(false);
  const frameRef = useRef(0);
  const pointerRef = useRef({ x: 0, y: 0 });

  function clearGlow() {
    if (frameRef.current) cancelAnimationFrame(frameRef.current);
    frameRef.current = 0;
    const card = cardRef.current;
    if (!card) return;
    card.dataset.uiGlowActive = 'false';
    card.style.removeProperty('--ui-pointer-x');
    card.style.removeProperty('--ui-pointer-y');
  }

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
    const update = () => {
      enabledRef.current = !reducedMotion.matches && finePointer.matches;
      if (!enabledRef.current) clearGlow();
    };
    update();
    reducedMotion.addEventListener('change', update);
    finePointer.addEventListener('change', update);
    return () => {
      enabledRef.current = false;
      reducedMotion.removeEventListener('change', update);
      finePointer.removeEventListener('change', update);
      clearGlow();
    };
  }, []);

  return <div
    data-slot="glow-card"
    className={className ? `ui-card ui-glow-card ${className}` : 'ui-card ui-glow-card'}
    ref={cardRef}
    onPointerMove={event => {
      onPointerMove?.(event);
      if (!enabledRef.current || event.defaultPrevented || event.pointerType === 'touch') return;
      pointerRef.current = { x: event.clientX, y: event.clientY };
      if (frameRef.current) return;
      frameRef.current = requestAnimationFrame(() => {
        frameRef.current = 0;
        const card = cardRef.current;
        if (!card || !enabledRef.current) return;
        const rect = card.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;
        const x = Math.max(0, Math.min(1, (pointerRef.current.x - rect.left) / rect.width));
        const y = Math.max(0, Math.min(1, (pointerRef.current.y - rect.top) / rect.height));
        card.style.setProperty('--ui-pointer-x', `${(x * 100).toFixed(2)}%`);
        card.style.setProperty('--ui-pointer-y', `${(y * 100).toFixed(2)}%`);
        card.dataset.uiGlowActive = 'true';
      });
    }}
    onPointerLeave={event => {
      onPointerLeave?.(event);
      clearGlow();
    }}
    {...props}
  />;
}
