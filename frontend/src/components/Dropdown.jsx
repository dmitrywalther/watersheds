// src/components/Dropdown.jsx
//
// Стилизованный dropdown для тёмной панели. Заменяет нативный <select>
// на кнопку + всплывающее меню с единым внешним видом на всех ОС.

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Check } from "lucide-react";
import { theme } from "../styles/theme";

export default function Dropdown({ icon: Icon, value, options, onChange, label }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  const current = options.find((o) => o.value === value) || options[0];

  useEffect(() => {
    function onDocClick(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    function onEsc(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, []);

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: open ? theme.bg.controlHover : theme.bg.control,
          backdropFilter: "blur(16px)",
          WebkitBackdropFilter: "blur(16px)",
          border: `1px solid ${open ? theme.border.accent : theme.border.subtle}`,
          borderRadius: theme.radius.pill,
          padding: "9px 14px",
          color: theme.text.primary,
          fontSize: 13,
          fontWeight: 500,
          cursor: "pointer",
          boxShadow: theme.shadow.control,
          transition: "background 0.15s ease, border-color 0.15s ease",
          fontFamily: theme.font.family,
          whiteSpace: "nowrap",
        }}
      >
        {Icon && <Icon size={14} color={theme.accent.water} strokeWidth={2.25} />}
        <span style={{ color: theme.text.secondary, fontWeight: 400 }}>{label}</span>
        <span>{current?.label}</span>
        <ChevronDown
          size={14}
          style={{
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 0.15s ease",
            color: theme.text.secondary,
          }}
        />
      </button>

      {open && (
        <div
          className="ws-fade-in"
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            right: 0,
            minWidth: "100%",
            background: theme.bg.control,
            backdropFilter: "blur(20px)",
            WebkitBackdropFilter: "blur(20px)",
            border: `1px solid ${theme.border.medium}`,
            borderRadius: theme.radius.md,
            padding: 6,
            boxShadow: theme.shadow.panel,
            zIndex: 10,
          }}
        >
          {options.map((opt) => {
            const active = opt.value === value;
            return (
              <div
                key={opt.value}
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                  padding: "8px 10px",
                  borderRadius: theme.radius.sm,
                  fontSize: 13,
                  color: active ? theme.text.primary : theme.text.secondary,
                  background: active ? theme.accent.waterSoft : "transparent",
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                }}
                onMouseEnter={(e) => {
                  if (!active) e.currentTarget.style.background = theme.bg.cardHover;
                }}
                onMouseLeave={(e) => {
                  if (!active) e.currentTarget.style.background = "transparent";
                }}
              >
                <span>{opt.label}</span>
                {active && <Check size={14} color={theme.accent.water} />}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
