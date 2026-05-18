declare module "*.mdx" {
  import type * as React from "react";
  const Component: (props: Record<string, unknown>) => React.ReactNode;
  export default Component;
}
