import { Composition } from "remotion";
import { Caption, Intro, Output, Outro } from "./Cards";

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Intro" component={Intro} durationInFrames={1} fps={30} width={1920} height={1080} />
    <Composition id="Outro" component={Outro} durationInFrames={1} fps={30} width={1920} height={1080} />
    <Composition id="Caption" component={Caption} durationInFrames={1} fps={30} width={1920} height={1080}
      defaultProps={{ text: "" }} />
    <Composition id="Output" component={Output} durationInFrames={1} fps={30} width={1920} height={1080}
      defaultProps={{ title: "", lines: [], note: "" }} />
  </>
);
