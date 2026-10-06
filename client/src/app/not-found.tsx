import { Button, Result } from "antd";
import Link from "next/link";

export default function NotFound() {
  return (
    <Result
      status="404"
      title="Not found"
      subTitle="This page doesn't exist or you don't have access to it."
      extra={
        <Link href="/">
          <Button type="primary">Go home</Button>
        </Link>
      }
    />
  );
}
