package com.snapscan.offline;

/** No Android/ORT/OpenCV dependency: exact tensor and CTC contracts. */
public final class OcrContractTest {
    static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
    public static void main(String[] args) {
        String[] dict = {"blank", "中", "文", " "};
        float[][] steps = {{0,.9f,.1f,0},{0,.8f,.2f,0},{1,0,0,0},{0,.7f,.3f,0},{0,.1f,.9f,0},{0,0,0,1}};
        OcrMath.Decoded decoded = OcrMath.decode(steps, dict);
        require("中中文 ".equals(decoded.text), "blank separates duplicate characters; trailing space survives");
        require(Math.abs(decoded.score - .875) < .00001, "confidence uses emitted steps only");
        require(OcrMath.decode(new float[][] {{1,0,0,0}}, dict).score == 0, "all blank score is zero");
        byte[] bgr = {(byte)255,0,(byte)128};
        float[] chw = OcrMath.normalizeBgr(bgr,1,1,2);
        require(chw.length == 6 && chw[0] == 1 && chw[2] == -1, "BGR channels are not swapped");
        require(chw[1] == 0 && chw[3] == 0 && chw[5] == 0, "right padding is float zero, not normalized black");
        require(OcrMath.recWidth(100,10)==480, "long line keeps dynamic width");
        require(OcrMath.recWidth(10,100)==320, "short line retains minimum width");
        int[] size = OcrMath.resize32(4096,3072,2000,false);
        require(size[0] == 1984 && size[1] == 1504, "RapidOCR reduction rounds each dimension to 32");
        double[][] box = {{0,0},{100,0},{100,10},{0,10}};
        double[][] expanded = OcrMath.unclipRectangle(box,1.6);
        double d = 1000*1.6/220;
        require(Math.abs(expanded[0][0]+d) < .0001 && Math.abs(expanded[2][1]-10-d)<.0001, "DB rectangle area/perimeter expansion");
        boolean rejected = false;
        try { OcrMath.decode(new float[][] {{1,0}},dict); } catch (IllegalArgumentException expected) { rejected=true; }
        require(rejected,"mismatched CTC classes must fail closed");
        require("𣇉".equals(OcrMath.decode(new float[][]{{0,1}},new String[]{"blank","𣇉"}).text),
                "supplementary CJK remains one dictionary symbol and a valid surrogate pair");
        rejected=false;
        try { OcrMath.resize32(1,4000,2000,false); } catch(IllegalArgumentException expected) { rejected=true; }
        require(rejected,"RapidOCR reduction must reject a dimension rounded to zero rather than silently distort the image");
        double[][] exact=OcrMath.unclipRectangleByDistance(box,2);
        require(exact[0][0]==-2&&exact[0][1]==-2&&exact[2][0]==102&&exact[2][1]==12,"explicit contour distance expands both local axes");
        require(box[0][0]==0&&box[2][0]==100,"expansion does not mutate coordinates");
        require(OcrMath.unclipRectangleByDistance(box,0)[2][1]==10,"zero expansion is identity");
        rejected=false;try{OcrMath.unclipRectangleByDistance(box,-1);}catch(IllegalArgumentException expected){rejected=true;}require(rejected,"negative distance fails explicitly");
        rejected=false;try{OcrMath.unclipRectangleByDistance(box,Double.NaN);}catch(IllegalArgumentException expected){rejected=true;}require(rejected,"nonfinite distance fails explicitly");
        System.out.println("PASS 17 offline OCR tensor/CTC/geometry contracts");
    }
}
