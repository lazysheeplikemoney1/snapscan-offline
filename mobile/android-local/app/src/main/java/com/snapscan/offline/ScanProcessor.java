package com.snapscan.offline;

import org.json.JSONArray;
import org.json.JSONObject;
import org.opencv.core.*;
import org.opencv.imgproc.Imgproc;
import java.util.*;

/** Offline observed-paper geometry and scan appearance. All confidence is heuristic.
 * Input ownership stays with the caller; Result owns only its two returned Mats.
 * Uses conventional OpenCV operations supported by the official Android 4.12 API.
 *
 * Geometry: reduce longest edge to 960, propose quadrilaterals from three Canny
 * maps and bright regions, then require real side transitions and light interiors.
 * The fallback uses Lab neutral-paper masks (chroma 6/10/16, gray >= 100),
 * close/open morphology and connected components. At least two observed sides
 * must separate paper from background. Frame cuts have no invented paper edge.
 * A small outward envelope retains stepped overlapping paper. Only the filled,
 * one-pixel-dilated mask exterior is whitened after warping; enclosed ink is safe.
 * A separate last fallback measures distance from dominant bright Lab colors
 * for tinted paper, and identifies near-frame cuts into a broad dark screen rim.
 * Its mask is proposal evidence only and NEVER erases cropped image pixels.
 * Dark paper, indistinct boundaries and separate overlapping documents remain
 * limitations. The caller must show warnings and allow manual correction.
 * Automatic candidates below 10% of the complete input are advisory only:
 * retain the full input rather than discard page text around an inner box.
 */
public final class ScanProcessor {
    private ScanProcessor() {}
    public static final class Result implements AutoCloseable {
        public final Mat cropped, enhanced;
        public final JSONObject detection, quality;
        public final JSONArray quad, warnings;
        public boolean hasQuad;
        private boolean closed;
        Result(Mat crop,Mat appearance,JSONObject d,JSONObject q,JSONArray points,JSONArray notes){
            cropped=crop;enhanced=appearance;detection=d;quality=q;quad=points;warnings=notes;hasQuad=points!=null;
        }
        public void close(){if(!closed){closed=true;cropped.release();enhanced.release();}}
    }
    private static final class Arena implements AutoCloseable {
        final List<Mat> mats=new ArrayList<>();
        <T extends Mat>T own(T m){mats.add(m);return m;}
        public void close(){for(int i=mats.size()-1;i>=0;i--)mats.get(i).release();}
    }
    private static final class Candidate {
        Point[] points;String method;double score;JSONObject metrics;Mat mask;String[] cuts,contentCuts;
        Candidate(Point[] q,String m,double s,JSONObject d){points=q;method=m;score=s;metrics=d;}
    }
    private static final class Proposal {
        final Point[] points;final double agreement;
        Proposal(Point[] points,double agreement){this.points=points;this.agreement=agreement;}
    }
    private static final class Pixels {
        final int w,h;final byte[] gray,edges;final float[] distance;float[] chroma;
        Pixels(Mat g,Mat e,Mat d){w=g.cols();h=g.rows();gray=new byte[w*h];edges=new byte[w*h];distance=new float[w*h];g.get(0,0,gray);e.get(0,0,edges);d.get(0,0,distance);}
        int at(byte[] a,int x,int y){return a[y*w+x]&255;}
        boolean bounds(int x,int y){return x>=0&&x<w&&y>=0&&y<h;}
    }
    private static double cross(Point a,Point b){return a.x*b.y-a.y*b.x;}
    private static Point minus(Point a,Point b){return new Point(a.x-b.x,a.y-b.y);}
    private static double length(Point a){return Math.hypot(a.x,a.y);}
    private static double area(Point[] p){double s=0;for(int i=0;i<4;i++)s+=cross(p[i],p[(i+1)%4]);return s/2;}
    private static double clamp(double v,double low,double high){return Math.max(low,Math.min(high,v));}
    private static double median(double[] a,int n){double[] b=Arrays.copyOf(a,n);Arrays.sort(b);return n%2==1?b[n/2]:(b[n/2-1]+b[n/2])/2;}
    private static double quantile(byte[] a,double q){int[] h=new int[256];for(byte b:a)h[b&255]++;int target=(int)Math.round((a.length-1)*q),n=0;for(int i=0;i<256;i++){n+=h[i];if(n>target)return i;}return 255;}
    private static Point[] ordered(Point[] input){
        Point[] p=new Point[4];double cx=0,cy=0;for(int i=0;i<4;i++){p[i]=new Point(input[i].x,input[i].y);cx+=p[i].x/4;cy+=p[i].y/4;}
        final double x=cx,y=cy;Arrays.sort(p,Comparator.comparingDouble(v->Math.atan2(v.y-y,v.x-x)));
        int start=0;for(int i=1;i<4;i++)if(p[i].x+p[i].y<p[start].x+p[start].y-1e-6||(Math.abs(p[i].x+p[i].y-p[start].x-p[start].y)<1e-6&&(p[i].y<p[start].y||(p[i].y==p[start].y&&p[i].x<p[start].x))))start=i;
        Point[] q=new Point[4];for(int i=0;i<4;i++)q[i]=p[(start+i)%4];return q;
    }
    private static void validate(Point[] q,int w,int h){
        if(q==null||q.length!=4)throw new IllegalArgumentException("四角必须是四组有限坐标");
        for(int i=0;i<4;i++){
            Point a=q[i],b=q[(i+1)%4],c=q[(i+2)%4];
            if(!Double.isFinite(a.x)||!Double.isFinite(a.y)||a.x<0||a.y<0||a.x>w-1||a.y>h-1)throw new IllegalArgumentException("四角不能超出图像边界");
            Point u=minus(b,a),v=minus(c,b);double lu=length(u),lv=length(v);
            if(lu<8||lv<8||cross(u,v)/(lu*lv)<.04)throw new IllegalArgumentException("四角需按左上、右上、右下、左下连续排列，不能自交或重复");
        }
        if(area(q)<Math.max(64,.001*w*h))throw new IllegalArgumentException("所选四边形面积过小");
    }
    private static Point[] manual(JSONArray a,int w,int h)throws Exception{
        if(a.length()!=4)throw new IllegalArgumentException("四角必须是四组有限坐标");Point[] q=new Point[4];
        for(int i=0;i<4;i++){JSONArray p=a.getJSONArray(i);if(p.length()!=2)throw new IllegalArgumentException("四角必须是四组有限坐标");q[i]=new Point(p.getDouble(0),p.getDouble(1));}
        validate(q,w,h);return ordered(q); // Reject crossings BEFORE normalizing the start corner.
    }
    private static JSONArray jsonPoints(Point[] p)throws Exception{if(p==null)return null;JSONArray a=new JSONArray();for(Point v:p)a.put(new JSONArray(new double[]{v.x,v.y}));return a;}
    private static String[] frameSides(Point[] q,int w,int h){
        String[] s=new String[4];for(int i=0;i<4;i++){Point a=q[i],b=q[(i+1)%4];if(Math.max(Math.abs(a.y),Math.abs(b.y))<=1.5)s[i]="top";else if(Math.max(Math.abs(a.x-w+1),Math.abs(b.x-w+1))<=1.5)s[i]="right";else if(Math.max(Math.abs(a.y-h+1),Math.abs(b.y-h+1))<=1.5)s[i]="bottom";else if(Math.max(Math.abs(a.x),Math.abs(b.x))<=1.5)s[i]="left";}return s;
    }
    /** Near-frame dark strips are visible display-content cuts, not inferred
     * off-image paper corners. This is used ONLY by the adaptive fallback. */
    private static String[] contentCutSides(Pixels p,Point[] q){
        String[] physical=frameSides(q,p.w,p.h),found=new String[4];
        for(int i=0;i<4;i++){
            if(physical[i]!=null)continue;Point first=q[i],second=q[(i+1)%4];String name=null;
            if(Math.max(Math.abs(first.y),Math.abs(second.y))<=Math.max(8,p.h*.07))name="top";
            else if(Math.max(Math.abs(first.x-p.w+1),Math.abs(second.x-p.w+1))<=Math.max(8,p.w*.07))name="right";
            else if(Math.max(Math.abs(first.y-p.h+1),Math.abs(second.y-p.h+1))<=Math.max(8,p.h*.07))name="bottom";
            else if(Math.max(Math.abs(first.x),Math.abs(second.x))<=Math.max(8,p.w*.07))name="left";
            if(name==null)continue;Point edge=minus(second,first);double length=length(edge),nx=-edge.y/length,ny=edge.x/length;
            double outerWidth=Math.max(6,Math.min(24,Math.min(p.w,p.h)*.04));double[] values=new double[300];int n=0,dark=0;
            for(int j=0;j<60;j++){
                double t=.08+.84*j/59.,sx=first.x+t*edge.x,sy=first.y+t*edge.y;
                for(int k=0;k<5;k++){
                    double offset=3+(outerWidth-3)*k/4.;int x=(int)Math.rint(sx-nx*offset),y=(int)Math.rint(sy-ny*offset);
                    if(!p.bounds(x,y))continue;int value=p.at(p.gray,x,y);values[n++]=value;if(value<80)dark++;
                }
            }
            if(n>=135&&dark/(double)n>=.75&&median(values,n)<65)found[i]=name;
        }return found;
    }
    private static String[] mergeCuts(String[] physical,String[] content){String[] cuts=physical.clone();for(int i=0;i<4;i++)if(content[i]!=null)cuts[i]=content[i];return cuts;}
    private static double histogramMedian(int[] histogram,int count){
        int first=(count-1)/2,second=count/2,total=0;double low=0;
        for(int value=0;value<histogram.length;value++){int next=total+histogram[value];if(total<=first&&next>first)low=value;if(next>second)return (low+value)/2;total=next;}return 0;
    }
    private static List<double[]> paperColorCenters(Pixels p,byte[] lab){
        int[] histogram=new int[4096];int eligible=0;
        for(int i=0;i<p.gray.length;i++){int gray=p.gray[i]&255,a=lab[3*i+1]&255,b=lab[3*i+2]&255;if(gray>=120&&gray<=245&&Math.hypot(a-128,b-128)<=48){histogram[(a/4)*64+b/4]++;eligible++;}}
        List<double[]> centers=new ArrayList<>();if(eligible<64)return centers;
        int[] mass=new int[4096];Integer[] indices=new Integer[4096];
        for(int y=0;y<64;y++)for(int x=0;x<64;x++){int index=y*64+x;indices[index]=index;for(int dy=-1;dy<=1;dy++)for(int dx=-1;dx<=1;dx++)if(y+dy>=0&&y+dy<64&&x+dx>=0&&x+dx<64)mass[index]+=histogram[(y+dy)*64+x+dx];}
        Arrays.sort(indices,(a,b)->{int d=Integer.compare(mass[b],mass[a]);return d!=0?d:Integer.compare(b,a);});
        for(int flat:indices){
            if(mass[flat]<Math.max(64,eligible*.02))break;double ca=(flat/64)*4+2,cb=(flat%64)*4+2;boolean close=false;
            for(double[] center:centers)if(Math.hypot(ca-center[0],cb-center[1])<8){close=true;break;}if(close)continue;
            int[] as=new int[256],bs=new int[256];int n=0;
            for(int i=0;i<p.gray.length;i++){int gray=p.gray[i]&255,a=lab[3*i+1]&255,b=lab[3*i+2]&255;if(gray>=120&&gray<=245&&Math.hypot(a-128,b-128)<=48&&Math.max(Math.abs(a-ca),Math.abs(b-cb))<=6){as[a]++;bs[b]++;n++;}}
            if(n<64)continue;double[] center={histogramMedian(as,n),histogramMedian(bs,n)};close=false;
            for(double[] previous:centers)if(Math.hypot(center[0]-previous[0],center[1]-previous[1])<8){close=true;break;}
            if(!close)centers.add(center);if(centers.size()==3)break;
        }return centers;
    }
    private static Mat kernel(Arena a,int n){return a.own(Imgproc.getStructuringElement(Imgproc.MORPH_RECT,new Size(n,n)));}
    private static Mat gray(Arena a,Mat image){Mat g=a.own(new Mat());Imgproc.cvtColor(image,g,Imgproc.COLOR_BGR2GRAY);return g;}
    private static Mat polygon(Arena a,Point[] p,int w,int h){Mat m=a.own(Mat.zeros(h,w,CvType.CV_8U));MatOfPoint ring=a.own(new MatOfPoint(p));Imgproc.fillConvexPoly(m,ring,new Scalar(255));return m;}
    private static JSONObject insideMetrics(Pixels p,Mat polygon,byte[] mask)throws Exception{
        try(Arena a=new Arena()){
            Mat inset=a.own(new Mat());Imgproc.erode(polygon,inset,kernel(a,7));byte[] selected=new byte[p.w*p.h];inset.get(0,0,selected);
            int n=0,bright=0,edges=0,fill=0;int[] hist=new int[256];
            for(int i=0;i<selected.length;i++)if(selected[i]!=0){n++;int v=p.gray[i]&255;hist[v]++;if(v>=130)bright++;if(p.edges[i]!=0)edges++;if(mask!=null&&mask[i]!=0)fill++;}
            int total=0,med=0;for(;med<255;med++){total+=hist[med];if(total>=Math.max(1,n/2))break;}
            return new JSONObject().put("pixels",n).put("bright",n==0?0:(double)bright/n).put("density",n==0?1:(double)edges/n).put("fill",n==0?0:(double)fill/n).put("median",med);
        }
    }
    private static Candidate score(Pixels p,Point[] q,double agreement,boolean clipped,byte[] mask,int limit)throws Exception{
        return score(p,q,agreement,clipped,mask,limit,null);
    }
    private static Candidate score(Pixels p,Point[] q,double agreement,boolean clipped,byte[] mask,int limit,String[] customCuts)throws Exception{
        double ar=area(q)/(p.w*p.h);if(ar<(clipped?.035:.02)||ar>(clipped?.90:.94))return null;
        Point[] edge=new Point[4];double[] len=new double[4];double shortest=1e9,longest=0;int near=0;
        for(int i=0;i<4;i++){edge[i]=minus(q[(i+1)%4],q[i]);len[i]=length(edge[i]);shortest=Math.min(shortest,len[i]);longest=Math.max(longest,len[i]);if(q[i].x<3||q[i].y<3||q[i].x>p.w-4||q[i].y>p.h-4)near++;}
        if(longest/shortest>(clipped?18:22)||(!clipped&&near>=3))return null;
        if(Math.max(len[0],len[2])/Math.min(len[0],len[2])>3||Math.max(len[1],len[3])/Math.min(len[1],len[3])>3)return null;
        for(int i=0;i<4;i++)if(cross(edge[i],edge[(i+1)%4])/(len[i]*len[(i+1)%4])<.25)return null;
        String[] cuts=customCuts==null?frameSides(q,p.w,p.h):customCuts;int cutCount=0;for(String s:cuts)if(s!=null)cutCount++;if(clipped&&(cutCount<1||cutCount>2))return null;
        double[] supports=new double[4],contrasts=new double[4],margins=new double[4],steps=new double[4];int ns=0,nc=0;
        double inset=clipped?Math.max(5,Math.min(14,shortest*.035)):Math.max(3,Math.min(8,shortest*.04));
        for(int i=0;i<4;i++){
            if(clipped&&cuts[i]!=null)continue;int samples=clipped?100:80,supported=0,valid=0,transitions=0;double[] inside=new double[samples],outside=new double[samples],insideChroma=new double[samples];
            Point normal=new Point(-edge[i].y/len[i],edge[i].x/len[i]);
            for(int j=0;j<samples;j++){
                double t=(clipped?.04:.05)+(clipped?.92:.90)*j/(samples-1),sx=q[i].x+t*edge[i].x,sy=q[i].y+t*edge[i].y;
                int x=(int)Math.round(sx),y=(int)Math.round(sy);if(p.distance[y*p.w+x]<=(clipped?Math.max(3,shortest*.025):2.8))supported++;
                int ix=(int)Math.round(sx+normal.x*inset),iy=(int)Math.round(sy+normal.y*inset),ox=(int)Math.round(sx-normal.x*inset),oy=(int)Math.round(sy-normal.y*inset);
                if(!p.bounds(ix,iy)||!p.bounds(ox,oy))continue;
                inside[valid]=p.at(p.gray,ix,iy);outside[valid]=p.at(p.gray,ox,oy);
                if(clipped){double ci=p.chroma[iy*p.w+ix],co=p.chroma[oy*p.w+ox];insideChroma[valid]=ci;if(co-ci>=6||inside[valid]-outside[valid]>=15)transitions++;}valid++;
            }
            supports[ns]=(double)supported/samples;
            if(clipped){if(valid<75||median(inside,valid)<125||median(insideChroma,valid)>limit+2)return null;steps[ns]=(double)transitions/valid;if(steps[ns]<.65||supports[ns]<.55)return null;}
            ns++;
            if(valid>=24){margins[nc]=median(inside,valid);contrasts[nc]=Math.abs(margins[nc]-median(outside,valid));nc++;}
        }
        double mean=0,minSupport=1;for(int i=0;i<ns;i++){mean+=supports[i]/ns;minSupport=Math.min(minSupport,supports[i]);}
        if(!clipped){int strong=0;for(int i=0;i<nc;i++)if(contrasts[i]>=6)strong++;if(nc<3||strong<3||median(contrasts,nc)<7||minSupport<.35||mean<.65||median(margins,nc)<135)return null;}
        try(Arena a=new Arena()){
            JSONObject in=insideMetrics(p,polygon(a,q,p.w,p.h),mask);double bright=in.getDouble("bright"),density=in.getDouble("density");if(in.getInt("pixels")<64)return null;
            double value;JSONObject metrics=new JSONObject().put("area_ratio",ar).put("interior_edge_density",density);
            if(clipped){double fill=in.getDouble("fill"),minStep=1;for(int i=0;i<ns;i++)minStep=Math.min(minStep,steps[i]);if(fill<.88||bright<.75||in.getInt("median")<145||density<.002||density>.20)return null;value=Math.min(.88,.25*minStep+.25*fill+.20*agreement+.15*mean+.15*bright);metrics.put("paper_mask_fill",fill).put("observed_boundary_support",mean).put("observed_color_transition",minStep).put("chroma_limit",limit);}
            else{if(bright<.70||in.getInt("median")<145||density>.28)return null;double contrast=median(contrasts,nc);value=.40*mean+.22*Math.min(contrast/35,1)+.16*bright+.12*agreement+.10*Math.min(Math.sqrt(ar/.5),1);if(value<.60)return null;metrics.put("boundary_support",mean).put("boundary_contrast",contrast).put("interior_bright_fraction",bright);}
            metrics.put("score",value);return new Candidate(q,clipped?"clipped_paper_region":"multi_edge",value,metrics);
        }
    }
    private static List<Proposal> proposals(Mat mask){
        List<MatOfPoint> contours=new ArrayList<>();Mat hierarchy=new Mat();List<Proposal> result=new ArrayList<>();
        try{
            Imgproc.findContours(mask,contours,hierarchy,Imgproc.RETR_LIST,Imgproc.CHAIN_APPROX_SIMPLE);contours.sort((a,b)->Double.compare(Imgproc.contourArea(b),Imgproc.contourArea(a)));
            for(int i=0;i<Math.min(100,contours.size());i++){
                MatOfPoint c=contours.get(i);double s=Imgproc.contourArea(c);if(s<.02*mask.total()||s>.96*mask.total())continue;
                try(Arena a=new Arena()){
                    MatOfPoint2f curve=a.own(new MatOfPoint2f(c.toArray())),approx=a.own(new MatOfPoint2f());double perimeter=Imgproc.arcLength(curve,true);
                    for(double eps:new double[]{.01,.015,.02,.03}){Imgproc.approxPolyDP(curve,approx,eps*perimeter,true);if(approx.total()!=4)continue;Point[] q=ordered(approx.toArray());try{validate(q,mask.cols(),mask.rows());}catch(IllegalArgumentException bad){continue;}double agreement=s/area(q);if(agreement>=.86&&agreement<=1.12){result.add(new Proposal(q,Math.min(agreement,1/agreement)));break;}}
                }
            }
        }finally{hierarchy.release();for(Mat c:contours)c.release();}return result;
    }
    private static Mat paperMask(Arena a,Mat image,Pixels p,int limit){
        byte[] bytes=new byte[p.w*p.h];for(int i=0;i<bytes.length;i++)if((p.gray[i]&255)>=100&&p.chroma[i]<=limit)bytes[i]=(byte)255;
        Mat mask=a.own(new Mat(p.h,p.w,CvType.CV_8U));mask.put(0,0,bytes);int size=Math.min(15,Math.max(5,((int)Math.round(Math.min(p.w,p.h)*.015))|1));Imgproc.morphologyEx(mask,mask,Imgproc.MORPH_CLOSE,kernel(a,size));Imgproc.morphologyEx(mask,mask,Imgproc.MORPH_OPEN,kernel(a,3));return mask;
    }
    private static Mat envelope(Arena owner,Candidate candidate,Mat mask)throws Exception{
        try(Arena a=new Arena()){
            Point[] q=candidate.points;Mat labels=a.own(new Mat());int n=Imgproc.connectedComponents(mask,labels);int[] ids=new int[(int)mask.total()],counts=new int[n];labels.get(0,0,ids);
            Mat poly=polygon(a,q,mask.cols(),mask.rows());byte[] inside=new byte[ids.length];poly.get(0,0,inside);for(int i=0;i<ids.length;i++)if(inside[i]!=0&&ids[i]>0)counts[ids[i]]++;
            int label=0;for(int i=1;i<n;i++)if(counts[i]>counts[label])label=i;if(label==0||counts[label]<64)return null;
            Point[] normals=new Point[4];double[] bounds=new double[4],minimum=new double[4],lengths=new double[4];Arrays.fill(minimum,Double.POSITIVE_INFINITY);String[] cuts=candidate.cuts==null?frameSides(q,mask.cols(),mask.rows()):candidate.cuts;
            for(int i=0;i<4;i++){Point e=minus(q[(i+1)%4],q[i]);lengths[i]=length(e);normals[i]=new Point(-e.y/lengths[i],e.x/lengths[i]);bounds[i]=normals[i].x*q[i].x+normals[i].y*q[i].y;}
            byte[] component=new byte[ids.length];for(int i=0;i<ids.length;i++)if(ids[i]==label){component[i]=(byte)255;double x=i%mask.cols(),y=i/mask.cols();for(int j=0;j<4;j++)minimum[j]=Math.min(minimum[j],x*normals[j].x+y*normals[j].y);}
            double shortest=Arrays.stream(lengths).min().getAsDouble(),maxExpansion=0;
            for(int i=0;i<4;i++)if(cuts[i]==null){double delta=Math.max(0,bounds[i]-minimum[i]);if(delta>Math.max(4,shortest*.05))return null;bounds[i]-=delta;maxExpansion=Math.max(maxExpansion,delta);}
            Point[] expanded=new Point[4];for(int i=0;i<4;i++){int j=(i+3)%4;double determinant=cross(normals[j],normals[i]);if(Math.abs(determinant)<1e-8)return null;expanded[i]=new Point(clamp((bounds[j]*normals[i].y-normals[j].y*bounds[i])/determinant,0,mask.cols()-1),clamp((normals[j].x*bounds[i]-bounds[j]*normals[i].x)/determinant,0,mask.rows()-1));}
            try{validate(expanded,mask.cols(),mask.rows());}catch(IllegalArgumentException bad){return null;}
            if(candidate.cuts!=null){
                Mat expandedPoly=polygon(a,expanded,mask.cols(),mask.rows());byte[] selected=new byte[ids.length];expandedPoly.get(0,0,selected);int covered=0,total=0;
                for(int i=0;i<ids.length;i++)if(ids[i]==label){total++;if(selected[i]!=0)covered++;}
                double coverage=covered/(double)Math.max(1,total);if(coverage<.99)return null;candidate.metrics.put("paper_mask_coverage",coverage);
            }
            candidate.points=expanded;candidate.metrics.put("envelope_max_expansion",maxExpansion);
            Mat selected=a.own(new Mat(mask.rows(),mask.cols(),CvType.CV_8U));selected.put(0,0,component);
            // Fill enclosed text holes, never classify dark letters as background.
            List<MatOfPoint> contours=new ArrayList<>();Mat hierarchy=a.own(new Mat());Imgproc.findContours(selected,contours,hierarchy,Imgproc.RETR_EXTERNAL,Imgproc.CHAIN_APPROX_SIMPLE);selected.setTo(new Scalar(0));Imgproc.drawContours(selected,contours,-1,new Scalar(255),Imgproc.FILLED);for(Mat c:contours)c.release();
            Imgproc.dilate(selected,selected,kernel(a,3));return owner.own(selected.clone());
        }
    }
    private static Candidate adaptivePaper(Arena owner,Mat image,Pixels p,byte[] lab)throws Exception{
        Candidate best=null;
        for(double[] center:paperColorCenters(p,lab)){
            p.chroma=new float[p.w*p.h];
            for(int i=0;i<p.chroma.length;i++)p.chroma[i]=(float)Math.hypot((lab[3*i+1]&255)-center[0],(lab[3*i+2]&255)-center[1]);
            for(int limit:new int[]{6,10}){
                Mat mask=paperMask(owner,image,p,limit);byte[] bytes=new byte[(int)mask.total()];mask.get(0,0,bytes);
                for(Proposal proposal:proposals(mask)){
                    String[] content=contentCutSides(p,proposal.points),cuts=mergeCuts(frameSides(proposal.points,p.w,p.h),content);
                    Candidate candidate=score(p,proposal.points,proposal.agreement,true,bytes,limit,cuts);if(candidate==null)continue;
                    candidate.cuts=cuts;candidate.contentCuts=content;candidate.mask=envelope(owner,candidate,mask);if(candidate.mask==null)continue;
                    candidate.method="adaptive_paper_region";candidate.score=Math.min(candidate.score,.86);
                    candidate.metrics.put("score",candidate.score).put("paper_color_ab",new JSONArray(center)).put("color_distance_limit",limit).put("color_basis","dominant_bright_region_lab");
                    if(best==null||candidate.score>best.score)best=candidate;
                }
            }
        }return best;
    }
    private static Candidate detect(Arena owner,Mat original)throws Exception{
        try(Arena a=new Arena()){
            double scale=Math.min(1,960.0/Math.max(original.rows(),original.cols()));Mat image=a.own(new Mat());if(scale<1)Imgproc.resize(original,image,new Size(Math.round(original.cols()*scale),Math.round(original.rows()*scale)),0,0,Imgproc.INTER_AREA);else original.copyTo(image);
            Mat g=gray(a,image),blur=a.own(new Mat()),all=a.own(Mat.zeros(g.size(),CvType.CV_8U)),distance=a.own(new Mat());Imgproc.GaussianBlur(g,blur,new Size(5,5),0);
            List<Mat> maps=new ArrayList<>();List<String> methods=new ArrayList<>();int[][] thresholds={{10,35},{30,90},{60,180}};
            for(int[] t:thresholds){Mat e=a.own(new Mat());Imgproc.Canny(blur,e,t[0],t[1]);Core.max(all,e,all);Imgproc.morphologyEx(e,e,Imgproc.MORPH_CLOSE,kernel(a,3));maps.add(e);methods.add("multi_edge");}
            Mat inverse=a.own(new Mat());Core.bitwise_not(all,inverse);Imgproc.distanceTransform(inverse,distance,Imgproc.DIST_L2,3);Pixels p=new Pixels(g,all,distance);
            Mat dummy=a.own(new Mat());double otsu=Imgproc.threshold(blur,dummy,0,255,Imgproc.THRESH_BINARY|Imgproc.THRESH_OTSU);byte[] blurred=new byte[(int)blur.total()];blur.get(0,0,blurred);Set<Integer> levels=new TreeSet<>();levels.add((int)otsu);for(double v:new double[]{.60,.75,.88,.94})levels.add((int)quantile(blurred,v));
            for(int t:levels)if(t>=80&&t<=250){Mat m=a.own(new Mat());Imgproc.threshold(blur,m,t,255,Imgproc.THRESH_BINARY);Imgproc.morphologyEx(m,m,Imgproc.MORPH_CLOSE,kernel(a,7));Imgproc.morphologyEx(m,m,Imgproc.MORPH_OPEN,kernel(a,3));maps.add(m);methods.add("paper_region");}
            Candidate best=null;Set<String> seen=new HashSet<>();int accepted=0;
            for(int i=0;i<maps.size();i++)for(Proposal proposal:proposals(maps.get(i))){Point[] q=proposal.points;StringBuilder key=new StringBuilder();for(Point v:q)key.append(Math.round(v.x/3)).append(',').append(Math.round(v.y/3)).append(';');if(!seen.add(key.toString()))continue;Candidate c=score(p,q,proposal.agreement,false,null,0);if(c!=null){accepted++;c.method=methods.get(i);if(best==null||c.score>best.score)best=c;}}
            if(best==null){
                Mat lab=a.own(new Mat());Imgproc.cvtColor(image,lab,Imgproc.COLOR_BGR2Lab);byte[] values=new byte[(int)lab.total()*3];lab.get(0,0,values);p.chroma=new float[p.w*p.h];for(int i=0;i<p.chroma.length;i++)p.chroma[i]=(float)Math.hypot((values[3*i+1]&255)-128,(values[3*i+2]&255)-128);
                for(int limit:new int[]{6,10,16}){Mat m=paperMask(a,image,p,limit);byte[] bytes=new byte[(int)m.total()];m.get(0,0,bytes);for(Proposal proposal:proposals(m)){Candidate c=score(p,proposal.points,proposal.agreement,true,bytes,limit);if(c==null)continue;c.mask=envelope(a,c,m);if(c.mask==null)continue;if(best==null||c.score>best.score)best=c;}}
                // Keep existing complete and neutral-paper decisions unchanged.
                // Relative color is an extra candidate source, not a relaxation.
                if(best==null)best=adaptivePaper(a,image,p,values);
                if(best!=null){best.mask=owner.own(best.mask.clone());accepted=1;}
            }
            if(best==null)return null;
            best.metrics.put("accepted_candidates",accepted);
            if(best.mask!=null){ // Transform mask in the same coordinate system as its fitted visible quad.
                best.metrics.put("work_quad",jsonPoints(best.points));
                for(Point v:best.points){v.x*=((double)original.cols()-1)/(p.w-1);v.y*=((double)original.rows()-1)/(p.h-1);}
            }else for(Point v:best.points){v.x=clamp(v.x/scale,0,original.cols()-1);v.y=clamp(v.y/scale,0,original.rows()-1);}
            validate(best.points,original.cols(),original.rows());return best;
        }
    }
    private static Mat warp(Mat input,Point[] quad,Size target,boolean binary){
        try(Arena a=new Arena()){
            MatOfPoint2f src=a.own(new MatOfPoint2f(quad)),dst=a.own(new MatOfPoint2f(new Point(0,0),new Point(target.width-1,0),new Point(target.width-1,target.height-1),new Point(0,target.height-1)));Mat h=a.own(Imgproc.getPerspectiveTransform(src,dst)),out=new Mat();
            Imgproc.warpPerspective(input,out,h,target,binary?Imgproc.INTER_NEAREST:Imgproc.INTER_CUBIC,binary?Core.BORDER_CONSTANT:Core.BORDER_REPLICATE,new Scalar(0));return out;
        }
    }
    private static Mat enhance(Mat bgr,String mode){
        if(mode.equals("color"))return bgr.clone();
        try(Arena a=new Arena()){
            Mat g=gray(a,bgr),small=a.own(new Mat());double ratio=Math.min(1,1400.0/Math.max(g.rows(),g.cols()));Imgproc.resize(g,small,new Size(Math.max(1,Math.round(g.cols()*ratio)),Math.max(1,Math.round(g.rows()*ratio))),0,0,Imgproc.INTER_AREA);
            int shortSide=Math.min(small.rows(),small.cols()),k=Math.max(15,Math.min(65,(shortSide/25)|1));Mat field=a.own(new Mat()),smooth=a.own(new Mat()),full=a.own(new Mat());Imgproc.morphologyEx(small,field,Imgproc.MORPH_CLOSE,kernel(a,k));field.convertTo(field,CvType.CV_32F);Imgproc.GaussianBlur(field,smooth,new Size(0,0),Math.max(3,Math.min(16,shortSide/48.0)));Imgproc.resize(smooth,full,bgr.size(),0,0,Imgproc.INTER_LINEAR);
            int n=(int)g.total();float[] background=new float[n];byte[] gray=new byte[n],bytes=new byte[n*3];full.get(0,0,background);g.get(0,0,gray);bgr.get(0,0,bytes);int[] curve=new int[256];for(int i=0;i<256;i++)curve[i]=(int)Math.round(255*Math.pow(i/255.,1.08));
            for(int i=0;i<n;i++){double gain=clamp(245/Math.max(48,Math.max(background[i],gray[i]&255)),.85,2.5);for(int c=0;c<3;c++)bytes[3*i+c]=(byte)curve[(int)Math.min(255,(bytes[3*i+c]&255)*gain)];}
            Mat clean=new Mat(bgr.rows(),bgr.cols(),CvType.CV_8UC3);clean.put(0,0,bytes);if(mode.equals("clean"))return clean;a.own(clean);
            Mat cg=gray(a,clean),f=a.own(new Mat()),statistics=a.own(new Mat()),mean=a.own(new Mat()),squared=a.own(new Mat()),meansq=a.own(new Mat());cg.convertTo(f,CvType.CV_32F);Imgproc.GaussianBlur(f,statistics,new Size(3,3),.65);int block=Math.max(31,Math.min(151,(Math.min(cg.rows(),cg.cols())/24)|1));Imgproc.boxFilter(statistics,mean,CvType.CV_32F,new Size(block,block));Core.multiply(statistics,statistics,squared);Imgproc.boxFilter(squared,meansq,CvType.CV_32F,new Size(block,block));float[] mv=new float[n],sv=new float[n];mean.get(0,0,mv);meansq.get(0,0,sv);cg.get(0,0,gray);
            for(int i=0;i<n;i++){double sd=Math.sqrt(Math.max(0,sv[i]-mv[i]*mv[i])),threshold=mv[i]*(1+.07*(sd/128-1))-1;gray[i]=(byte)((gray[i]&255)>threshold?255:0);}
            Mat binary=a.own(new Mat(cg.rows(),cg.cols(),CvType.CV_8U));binary.put(0,0,gray);Mat result=new Mat();Imgproc.cvtColor(binary,result,Imgproc.COLOR_GRAY2BGR);return result;
        }
    }
    private static JSONObject quality(Mat image)throws Exception{
        try(Arena a=new Arena()){
            Mat g=gray(a,image);double ratio=Math.min(1,1200.0/Math.max(g.rows(),g.cols()));if(ratio<1)Imgproc.resize(g,g,new Size(Math.round(g.cols()*ratio),Math.round(g.rows()*ratio)),0,0,Imgproc.INTER_AREA);
            Mat lap=a.own(new Mat());Imgproc.Laplacian(g,lap,CvType.CV_64F);MatOfDouble mean=a.own(new MatOfDouble()),std=a.own(new MatOfDouble());Core.meanStdDev(lap,mean,std);double detail=Math.pow(std.toArray()[0],2);byte[] values=new byte[(int)g.total()];g.get(0,0,values);double brightness=quantile(values,.5),contrast=quantile(values,.95)-quantile(values,.05);JSONArray warnings=new JSONArray();if(detail<35)warnings.put("细节较少，可能模糊或文字过小；建议放大核对，必要时靠近重拍");if(brightness<70)warnings.put("整体偏暗，建议改善照明并避开手部阴影");if(contrast<25)warnings.put("文字与背景对比较低，建议核对小字或尝试黑白模式");
            return new JSONObject().put("blur_score",Math.round(detail*1000)/1000.).put("is_blurry",detail<35).put("median_brightness",brightness).put("contrast_p95_p05",contrast).put("low_light",brightness<70).put("low_contrast",contrast<25).put("quality_gate","advisory_only").put("thresholds_calibrated",false).put("analysis_max_edge",1200).put("warnings",warnings);
        }
    }
    public static Result process(Mat bgr,JSONArray manualQuad,String mode)throws Exception{
        if(bgr==null||bgr.empty()||bgr.depth()!=CvType.CV_8U||bgr.channels()!=3||Math.min(bgr.rows(),bgr.cols())<16)throw new IllegalArgumentException("扫描图必须是非空三通道 BGR 图像");
        if(!Arrays.asList("color","clean","bw").contains(mode))throw new IllegalArgumentException("扫描模式必须为 color、clean 或 bw");
        Mat crop=null,appearance=null;
        try(Arena a=new Arena()){
            Candidate c=manualQuad==null?detect(a,bgr):new Candidate(manual(manualQuad,bgr.cols(),bgr.rows()),"manual",1,new JSONObject());
            // This policy applies at the single automatic output boundary,
            // after candidates use original-image coordinates, before any warp.
            // An explicit manual selection is intentionally exempt.
            double candidateAreaRatio=c==null?0:Math.abs(area(c.points))/((double)bgr.cols()*bgr.rows());
            boolean tooSmall=manualQuad==null&&c!=null&&candidateAreaRatio<.10;
            JSONObject d=new JSONObject().put("confidence_kind","heuristic_not_probability"),q;JSONArray warnings=new JSONArray();Point[] points=c==null||tooSmall?null:c.points;
            if(c==null||tooSmall){
                crop=bgr.clone();d.put("method","none").put("confidence",0);
                if(tooSmall){
                    String warning="自动候选区域小于整图的10%，已保留整图；请手动调整四角。";
                    c.metrics.put("candidate_method",c.method).put("candidate_confidence",Math.round(c.score*10000)/10000.).put("candidate_quad",jsonPoints(c.points));
                    d.put("rejection_reason","candidate_too_small").put("candidate_area_ratio",candidateAreaRatio).put("minimum_area_ratio",.10).put("diagnostics",c.metrics).put("warnings",new JSONArray().put(warning));
                    warnings.put(warning);
                }else warnings.put("未检测到可靠纸张边界，请手动调整四角或重新拍摄");
            }
            else{
                double w=Math.max(length(minus(points[2],points[3])),length(minus(points[1],points[0]))),h=Math.max(length(minus(points[1],points[2])),length(minus(points[0],points[3])));crop=warp(bgr,points,new Size(Math.round(w)+1,Math.round(h)+1),false);d.put("method",c.method).put("confidence",Math.round(c.score*10000)/10000.);
                if(c.mask!=null){
                    boolean adaptive=c.method.equals("adaptive_paper_region");
                    if(adaptive){
                        // Relative-color masks can contain moire holes and
                        // shadowed paper-edge gaps. Never turn these into white
                        // pixels: use the quad only and retain the full warp.
                        c.metrics.remove("work_quad");d.put("background_pixels_whitened",0).put("background_cleanup","disabled_adaptive_mask_is_proposal_only");
                    }else{
                        Point[] work=manual(c.metrics.getJSONArray("work_quad"),c.mask.cols(),c.mask.rows());c.metrics.remove("work_quad");Mat visible=a.own(warp(c.mask,work,crop.size(),true)),outside=a.own(new Mat());Core.compare(visible,new Scalar(0),outside,Core.CMP_EQ);crop.setTo(new Scalar(255,255,255),outside);d.put("background_pixels_whitened",Core.countNonZero(outside)).put("background_cleanup","paper_mask_exterior_only");
                    }
                    JSONArray cuts=new JSONArray();for(String s:c.cuts==null?frameSides(points,bgr.cols(),bgr.rows()):c.cuts)if(s!=null)cuts.put(s);
                    JSONArray notes=new JSONArray().put(adaptive?"纸面在照片或屏幕边界处可能被截断，仅保留可见部分，无法恢复画外内容。":"纸张有部分超出画面，仅保留可见纸面，无法恢复画外内容。").put("范围按相连纸面整体估计，可能包含重叠纸张，请检查四角。");
                    d.put("document_complete",false).put("clipped_edges",cuts).put("overlap_possible",true).put("overlap_handling","visible_paper_union").put("warnings",notes);
                    if(c.contentCuts!=null){JSONArray content=new JSONArray();for(String s:c.contentCuts)if(s!=null)content.put(s);d.put("content_cut_edges",content);}
                    for(int i=0;i<notes.length();i++)warnings.put(notes.get(i));
                }
                d.put("diagnostics",c.metrics);
            }
            q=quality(crop);JSONArray advisory=q.getJSONArray("warnings");for(int i=0;i<advisory.length();i++)warnings.put(advisory.get(i));appearance=enhance(crop,mode);return new Result(crop,appearance,d,q,jsonPoints(points),warnings);
        }catch(Exception|Error failure){if(crop!=null)crop.release();if(appearance!=null)appearance.release();throw failure;}
    }
}
