package com.snapscan.offline;

import android.content.Context;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import org.json.*;

/** Canonical documents, revision-checked corrections, and literal offline search. */
public final class DocumentStore extends SQLiteOpenHelper {
    public DocumentStore(Context context){super(context,"snapscan-local.db",null,1);}
    @Override public void onCreate(SQLiteDatabase db){db.execSQL("CREATE TABLE documents(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at INTEGER NOT NULL,filename TEXT NOT NULL,text TEXT NOT NULL,boxes TEXT NOT NULL,original_boxes TEXT NOT NULL,model TEXT NOT NULL,image TEXT NOT NULL,image_width INTEGER NOT NULL,image_height INTEGER NOT NULL,elapsed_ms INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 0)");}
    @Override public void onUpgrade(SQLiteDatabase db,int oldVersion,int newVersion){throw new IllegalStateException("缺少本地数据库升级规则");}
    public synchronized JSONObject insert(JSONObject result,String image,int width,int height,String filename,long elapsed)throws Exception {
        ContentValues values=new ContentValues();values.put("created_at",System.currentTimeMillis()/1000);values.put("filename",filename);
        values.put("text",TextLayout.apply(result).getString("text"));values.put("boxes",result.getJSONArray("boxes").toString());values.put("original_boxes",result.getJSONArray("boxes").toString());
        values.put("model",result.getJSONObject("model").toString());values.put("image",image);values.put("image_width",width);values.put("image_height",height);values.put("elapsed_ms",elapsed);values.put("revision",0);
        long id=getWritableDatabase().insertOrThrow("documents",null,values);return get(id);
    }
    public synchronized JSONObject get(long id)throws Exception {
        try(Cursor cursor=getReadableDatabase().rawQuery("SELECT * FROM documents WHERE id=?",new String[]{String.valueOf(id)})){
            if(!cursor.moveToFirst())throw new NativeApi.ApiException(404,"本机文档不存在");JSONObject out=new JSONObject();
            for(int i=0;i<cursor.getColumnCount();i++){
                String name=cursor.getColumnName(i);
                if(name.equals("boxes")||name.equals("original_boxes"))out.put(name,new JSONArray(cursor.getString(i)));
                else if(name.equals("model"))out.put(name,new JSONObject(cursor.getString(i)));
                else if(cursor.getType(i)==Cursor.FIELD_TYPE_INTEGER)out.put(name,cursor.getLong(i));
                else out.put(name,cursor.getString(i));
            }return TextLayout.apply(out);
        }
    }
    public synchronized JSONArray list(String query,int limit)throws Exception {
        JSONArray items=new JSONArray();int cap=Math.max(1,Math.min(200,limit));
        try(Cursor c=getReadableDatabase().rawQuery("SELECT id,created_at,filename,substr(text,1,200),elapsed_ms FROM documents WHERE instr(lower(text),lower(?))>0 ORDER BY id DESC LIMIT ?",new String[]{query,String.valueOf(cap)})){
            while(c.moveToNext())items.put(new JSONObject().put("id",c.getLong(0)).put("created_at",c.getLong(1)).put("filename",c.getString(2)).put("snippet",c.getString(3)).put("elapsed_ms",c.getLong(4)));
        }return items;
    }
    public synchronized JSONObject save(long id,JSONArray texts,int revision)throws Exception {
        SQLiteDatabase db=getWritableDatabase();db.beginTransaction();
        try{
            JSONObject current=get(id);if(current.getInt("revision")!=revision)throw new NativeApi.ApiException(409,"文档已更新，请重新打开后保存");
            JSONArray boxes=current.getJSONArray("boxes");if(boxes.length()==0||texts.length()!=boxes.length())throw new NativeApi.ApiException(422,"纠错行数必须与识别框数量一致");
            for(int i=0;i<boxes.length();i++){Object value=texts.get(i);if(!(value instanceof String))throw new NativeApi.ApiException(422,"校对文字必须为字符串");String line=(String)value;if(line.length()>100000)throw new NativeApi.ApiException(422,"单行文字过长");boxes.getJSONObject(i).put("text",line);}
            ContentValues values=new ContentValues();values.put("text",TextLayout.build(boxes).getString("text"));values.put("boxes",boxes.toString());values.put("revision",revision+1);
            if(db.update("documents",values,"id=? AND revision=?",new String[]{String.valueOf(id),String.valueOf(revision)})!=1)throw new NativeApi.ApiException(409,"文档已更新");
            JSONObject updated=get(id);db.setTransactionSuccessful();return updated;
        }finally{db.endTransaction();}
    }
}
