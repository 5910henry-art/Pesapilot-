import React from "react";

export function Skeleton({
  width = "100%",
  height = 14,
  radius = 8,
  className = "",
}) {
  return (
    <span
      className={`skeleton skeleton-block ${className}`}
      style={{ width, height, borderRadius: radius }}
      aria-hidden="true"
    />
  );
}

export function SkeletonText({ lines = 2, className = "" }) {
  return (
    <div className={`skeleton-text-stack ${className}`} aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton
          key={index}
          width={index === lines - 1 ? "58%" : "88%"}
          height={11}
        />
      ))}
    </div>
  );
}

export function SkeletonStatCard() {
  return (
    <div className="skeleton-stat-card" aria-hidden="true">
      <Skeleton width="45%" height={11} />
      <Skeleton width="72%" height={25} />
      <Skeleton width="55%" height={10} />
    </div>
  );
}

export function SkeletonRows({ rows = 4, avatar = true }) {
  return (
    <div className="skeleton-rows" aria-hidden="true">
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton-row" key={index}>
          {avatar && <Skeleton width={40} height={40} radius="50%" />}
          <div className="skeleton-row-content">
            <Skeleton width="65%" height={12} />
            <Skeleton width="42%" height={10} />
          </div>
          <Skeleton width={64} height={18} />
        </div>
      ))}
    </div>
  );
}
